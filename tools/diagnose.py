"""对照诊断：同一浏览器分别加载"线上原地址"和"本地镜像"，定位问题在哪一侧。

用法：
    python tools/diagnose.py --url <线上地址> --local <本地目录>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 允许 SwiftShader 软件渲染 —— Unity WebGL 需要真实 WebGL 上下文
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader "
    "--autoplay-policy=no-user-gesture-required --log-level=3"
)

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

MSGS = []
NET_FAIL = []


class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, src):
        MSGS.append(f"{msg}")

    def certificateError(self, err):
        return True


PROBE = r"""
(function(){
  function deep(doc, path, depth){
    if (depth > 3) return null;
    try {
      var w = doc.defaultView || {};
      if (w.unityInstance) return {engine:'unity', ready:!!w.unityInstance.Module,
        detail: path+' Module='+(!!w.unityInstance.Module)};
    } catch(e){}
    try {
      var cv = doc.querySelector('canvas');
      if (cv) {
        var gl = null;
        try { gl = cv.getContext('webgl2') || cv.getContext('webgl'); } catch(e){}
        return {engine:'canvas', ready: !!(cv.width>1&&cv.height>1),
          detail: path+' canvas='+cv.width+'x'+cv.height+' webgl='+!!gl,
          progress: (function(){ var p=doc.querySelector('#unity-progress-bar-full');
            return p ? p.style.width : ''; })()};
      }
    } catch(e){}
    var fs = doc.querySelectorAll('iframe,frame');
    for (var i=0;i<fs.length;i++){
      try { var fd = fs[i].contentDocument;
        if (fd) { var r = deep(fd, path+'>'+i, depth+1); if (r) return r; } } catch(e){}
    }
    return null;
  }
  var r = deep(document, 'top', 0) || {engine:'none', ready:false, detail:'nothing'};
  r.webglTop = (function(){ try { var c=document.createElement('canvas');
    return !!(c.getContext('webgl2')||c.getContext('webgl')); } catch(e){ return false; } })();
  return JSON.stringify(r);
})();
"""


def run(url: str, label: str, seconds: float, shot: str) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    view = QWebEngineView()
    page = Page(view)
    view.setPage(page)
    for a in ("JavascriptEnabled", "LocalStorageEnabled", "PluginsEnabled",
              "AllowRunningInsecureContent", "WebGLEnabled"):
        try:
            page.settings().setAttribute(getattr(QWebEngineSettings, a), True)
        except Exception:
            pass
    try:
        page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    except Exception:
        pass
    view.resize(1024, 640)
    view.show()

    out = {"label": label, "url": url}
    t0 = time.time()

    def finish():
        timer.stop()
        out["elapsed"] = round(time.time() - t0, 1)
        out["errors"] = [m for m in MSGS if "error" in m.lower() or "failed" in m.lower()][:8]
        out["msg_count"] = len(MSGS)
        try:
            view.grab().save(shot)
            out["shot"] = shot
        except Exception:
            pass
        print(json.dumps(out, ensure_ascii=False, indent=2))
        app.quit()

    def probe():
        def got(v):
            try:
                d = json.loads(v) if isinstance(v, str) else (v or {})
            except Exception:
                d = {}
            out.update({"engine": d.get("engine"), "ready": d.get("ready"),
                        "detail": d.get("detail"), "progress": d.get("progress"),
                        "webgl_top": d.get("webglTop")})
            if d.get("ready") or time.time() - t0 > seconds:
                finish()
        page.runJavaScript(PROBE, got)

    timer = QTimer()
    timer.timeout.connect(probe)
    timer.start(2000)
    view.load(QUrl(url))
    QTimer.singleShot(int(seconds * 1000) + 5000, finish)
    app.exec()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--local", default="")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--outdir", default=r"G:\traetool\temp\hgd-work")
    a = ap.parse_args()

    # 1) 线上原地址（对照组）
    live = run(a.url, "LIVE", a.seconds / 2, os.path.join(a.outdir, "diag_live.png"))

    # 2) 本地镜像
    if a.local:
        from hgd.core.mirror_server import MirrorServer
        from hgd.core.mirror import Downloader
        from urllib.parse import urlparse
        p = urlparse(a.url)
        base = f"{p.scheme}://{p.netloc}" + os.path.dirname(p.path)
        root = os.path.abspath(a.local)
        srv = MirrorServer(root=root, base_url=base,
                           downloader=Downloader(root, referer=base + "/"), lazy=True)
        port = srv.start()
        # 找到 index.html
        entry = "index.html" if os.path.exists(os.path.join(root, "index.html")) else ""
        if not entry:
            for r, _d, fs in os.walk(root):
                for f in fs:
                    if f.lower() in ("index.html", "index.htm"):
                        entry = os.path.relpath(os.path.join(r, f), root).replace("\\", "/")
                        break
                if entry:
                    break
        local = run(srv.url_for(entry), "MIRROR", a.seconds,
                    os.path.join(a.outdir, "diag_mirror.png"))
        local["lazy_fetched"] = srv.lazy_count
        srv.stop()
        print("\n===== 对照结论 =====")
        print("LIVE  ready:", live.get("ready"), "|", live.get("detail"))
        print("MIRROR ready:", local.get("ready"), "|", local.get("detail"))


if __name__ == "__main__":
    main()
