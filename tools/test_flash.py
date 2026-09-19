"""验证 Ruffle 能否真正加载并播放 swf。

构造一个与下载产物结构一致的游戏目录，然后用浏览器加载，检查
ruffle-player 元素是否创建、swf 是否成功加载（无致命错误）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader "
    "--autoplay-policy=no-user-gesture-required --log-level=3"
)

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from hgd.core.downloader import _ensure_ruffle, _FLASH_HTML, _esc
from hgd.core.mirror_server import MirrorServer
from hgd.core.mirror import Downloader

MSGS = []


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, src):
        MSGS.append(msg)


PROBE = r"""
(function(){
  var rp = document.querySelector('ruffle-player');
  if (!rp) return JSON.stringify({found:false, detail:'no ruffle-player element'});
  // Ruffle 把 canvas 放在 shadow DOM 里，必须穿透查询
  var root = rp.shadowRoot || rp;
  var cv = root.querySelector('canvas');
  var rect = rp.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    hasCanvas: !!cv,
    canvasSize: cv ? (cv.width + 'x' + cv.height) : 'none',
    w: Math.round(rect.width), h: Math.round(rect.height),
    shadow: !!rp.shadowRoot,
    detail: 'rect=' + Math.round(rect.width) + 'x' + Math.round(rect.height) +
            ' canvas=' + (cv ? cv.width + 'x' + cv.height : 'none')
  });
})();
"""


def main() -> int:
    workdir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else r"G:\traetool\temp\hgd-work\flash_test")
    swf = os.path.join(workdir, "game.swf")
    if not os.path.exists(swf):
        print("FAIL 缺少测试 swf:", swf)
        return 2

    # 造一个与真实下载产物一致的结构
    gdir = os.path.join(workdir, "FlashGame")
    shutil.rmtree(gdir, ignore_errors=True)
    os.makedirs(gdir, exist_ok=True)
    shutil.copy2(swf, os.path.join(gdir, "game.swf"))
    ctx = {
        "title": _esc("Flash 测试"),
        "ruffle_js": _ensure_ruffle(gdir),
        "swf": "game.swf",
        "ratio": 4 / 3,
        "width": 640, "height": 480,
    }
    html = _FLASH_HTML.format(**ctx)
    open(os.path.join(gdir, "index.html"), "w", encoding="utf-8").write(html)
    print("[flash] 生成的包装页 ruffle_js =", ctx["ruffle_js"])

    ruffle_ok = os.path.exists(os.path.join(gdir, "_runtime", "ruffle", "ruffle.js"))
    print("[flash] ruffle.js 存在:", ruffle_ok)
    wasm = [f for f in os.listdir(os.path.join(gdir, "_runtime", "ruffle")) if f.endswith(".wasm")]
    print("[flash] wasm 文件:", wasm)

    srv = MirrorServer(root=gdir, base_url="", downloader=Downloader(gdir), lazy=False)
    port = srv.start()
    url = srv.url_for("index.html")
    print("[flash] 服务:", url)

    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = Page(view)
    view.setPage(page)
    try:
        page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    except Exception:
        pass
    view.resize(800, 600)
    view.show()

    out = {"ok": False}
    t0 = time.time()

    def finish():
        timer.stop()
        out["errors"] = [m for m in MSGS if "rror" in m][:6]
        out["elapsed"] = round(time.time() - t0, 1)
        view.grab().save(os.path.join(workdir, "flash_shot.png"))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print("VERDICT:", "PASS" if out["ok"] else "FAIL")
        srv.stop()
        app.quit()

    def probe():
        def got(v):
            try:
                d = json.loads(v) if isinstance(v, str) else (v or {})
            except Exception:
                d = {}
            out.update(d)
            if d.get("hasCanvas") and d.get("w", 0) > 10:
                out["ok"] = True
                finish()
            elif time.time() - t0 > 25:
                finish()
        page.runJavaScript(PROBE, got)

    timer = QTimer(); timer.timeout.connect(probe); timer.start(1200)
    view.load(QUrl(url))
    QTimer.singleShot(32000, finish)
    app.exec()
    srv.stop()
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
