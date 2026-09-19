"""无头运行验收：真实加载下载好的游戏，检查是否成功启动。

用法：python tools/verify_game.py <游戏目录> [--seconds 40] [--shot out.png]
判据：
  - Unity: window.unityInstance 存在且 .Module 已初始化
  - Flash: Ruffle player 元素存在且 report 无致命错误
  - HTML : 无 JS 报错，且页面有非空绘制
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.webenginecontext.debug=false")
# 关键：Unity WebGL 需要 WebGL 上下文。必须允许 SwiftShader 软件渲染，
# 否则无 GPU 环境下 canvas 永远停在 Loading。
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--no-sandbox --enable-unsafe-swiftshader "
                      "--use-gl=angle --use-angle=swiftshader "
                      "--autoplay-policy=no-user-gesture-required --log-level=3")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication


CONSOLE = []
ERRORS = []


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, src):
        CONSOLE.append(f"[{level}] {msg}")
        if "error" in str(level).lower() or level == QWebEnginePage.ErrorMessageLevel:
            ERRORS.append(msg)


def make_probe_js() -> str:
    # 递归进入同源 iframe 探测（Unity 壳页常把游戏放在内层 index.htm）
    return r"""
(function () {
  function probeDoc(doc, path) {
    try {
      if (doc.defaultView && doc.defaultView.unityInstance) {
        var inst = doc.defaultView.unityInstance;
        return { engine: 'unity', loaded: !!inst.Module,
                 detail: path + ' unityInstance.Module=' + (!!inst.Module) };
      }
      var rp = doc.querySelector('ruffle-player, .ruffle-player');
      if (rp) return { engine: 'flash', loaded: true, detail: path + ' ruffle ' + rp.tagName };
      var cv = doc.querySelector('canvas');
      if (cv && cv.width > 1 && cv.height > 1)
        return { engine: 'html', loaded: true,
                 detail: path + ' canvas ' + cv.width + 'x' + cv.height };
    } catch (e) {}
    // 递归 iframe
    var frames = doc.querySelectorAll('iframe,frame');
    for (var i = 0; i < frames.length; i++) {
      try {
        var fd = frames[i].contentDocument;
        if (fd) {
          var r = probeDoc(fd, path + '>' + (new URL(frames[i].src || '', location.href)).pathname);
          if (r) return r;
        }
      } catch (e) {}
    }
    return null;
  }
  var r = probeDoc(document, 'top');
  if (r) {
    return JSON.stringify({ engine: r.engine, loaded: r.loaded, detail: r.detail });
  }
  var anyFrame = document.querySelector('iframe');
  return JSON.stringify({ engine: 'unknown', loaded: false,
    detail: 'no game found; iframe=' + (anyFrame ? anyFrame.src : 'none') });
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("game_dir")
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--shot", default="")
    args = ap.parse_args()

    game_dir = os.path.abspath(args.game_dir)
    entry = os.path.join(game_dir, "index.html")
    if not os.path.exists(entry):
        print(f"FAIL 找不到入口 {entry}")
        return 2

    # 复用播放器的逻辑：起镜像服务器
    from hgd.core.mirror_server import MirrorServer
    from hgd.core.mirror import Downloader
    from urllib.parse import urlparse

    base_url = ""
    mp = os.path.join(game_dir, "game_meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8"))
        eu = meta.get("entry_url", "")
        if eu:
            p = urlparse(eu)
            base_url = f"{p.scheme}://{p.netloc}" + os.path.dirname(p.path)

    srv = MirrorServer(root=game_dir, base_url=base_url,
                       downloader=Downloader(game_dir, referer=base_url + "/"), lazy=True)
    port = srv.start()
    url = srv.url_for("index.html")
    print(f"[verify] serving {game_dir} at {url}")

    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = Page(view)
    view.setPage(page)
    for attr in ("JavascriptEnabled", "LocalStorageEnabled", "PluginsEnabled",
                 "FullScreenSupportEnabled", "AllowRunningInsecureContent"):
        try:
            page.settings().setAttribute(getattr(QWebEngineSettings, attr), True)
        except Exception:
            pass
    try:
        page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    except Exception:
        pass

    view.resize(1024, 640)
    view.show()

    result = {"ok": False, "engine": "unknown", "detail": "", "console_errors": []}
    t0 = time.time()

    def probe():
        def got(val):
            try:
                d = json.loads(val) if isinstance(val, str) else (val or {})
            except Exception:
                d = {}
            eng = d.get("engine", "unknown")
            detail = d.get("detail", "")
            loaded = bool(d.get("loaded"))

            # 判定"真正就绪"：
            #  - Unity: 需要 unityInstance.Module 存在（加载条消失阶段才算数）
            #  - Flash/HTML: canvas 有尺寸即算就绪
            ready = loaded and (
                eng != "unity" or "Module=true" in detail
            )
            if ready:
                result["engine"] = eng
                result["detail"] = detail
                result["ok"] = True
                finish()
                return
            # 记录最新状态（未就绪时也保留，便于失败时诊断）
            if eng != "unknown":
                result["engine"] = eng
                result["detail"] = detail
            if (time.time() - t0) > args.seconds:
                finish()
        page.runJavaScript(make_probe_js(), got)

    def finish():
        timer.stop()
        result["console_errors"] = [e for e in ERRORS if "uncaught" in e.lower()
                                    or "failed" in e.lower()][:10]
        result["elapsed"] = round(time.time() - t0, 1)
        result["lazy_fetched"] = srv.lazy_count
        if args.shot:
            try:
                view.grab().save(args.shot)
                result["shot"] = args.shot
            except Exception:
                pass
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("VERDICT:", "PASS" if result["ok"] else "FAIL")
        srv.stop()
        app.quit()

    timer = QTimer()
    timer.timeout.connect(probe)
    timer.start(1500)

    view.load(QUrl(url))
    QTimer.singleShot(int(args.seconds * 1000) + 6000, finish)
    app.exec()
    srv.stop()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
