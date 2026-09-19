"""请求审计：记录本地镜像服务收到的每个请求及其结局（命中/懒补成功/失败）。

用途：定位"游戏缺资源"类问题——哪些 URL 在本地没有、回源又失败了。
"""
from __future__ import annotations

import argparse
import json
import os
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from hgd.core.mirror_server import MirrorServer, _GameHandler
from hgd.core.mirror import Downloader

HITS = []      # (path, existed_before, url_tried, ok_after)


class AuditingHandler(_GameHandler):
    def do_GET(self):
        local = self.translate_path(self.path)
        existed = os.path.exists(local)
        url = self.mirror.reverse_map(local)
        if not existed or os.path.isdir(local):
            self._lazy_fetch(local)
        after = os.path.exists(local)
        HITS.append((self.path, existed, url, after))
        super(_GameHandler, self).do_GET() if False else _GameHandler.do_GET(self)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_dir")
    ap.add_argument("--seconds", type=float, default=40)
    a = ap.parse_args()

    root = os.path.abspath(a.game_dir)
    base = ""
    mp = os.path.join(root, "game_meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8"))
        eu = meta.get("entry_url", "")
        from urllib.parse import urlparse
        p = urlparse(eu)
        base = f"{p.scheme}://{p.netloc}" + os.path.dirname(p.path)

    srv = MirrorServer(root=root, base_url=base,
                       downloader=Downloader(root, referer=base + "/"), lazy=True)

    # 换用审计 handler
    import hgd.core.mirror_server as ms_mod
    ms_mod._GameHandler = AuditingHandler
    port = srv.start()
    url = srv.url_for("index.html")
    print(f"[audit] {url}")

    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    try:
        page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    except Exception:
        pass
    view.resize(1024, 640)
    view.show()
    view.load(QUrl(url))

    def done():
        view.page().runJavaScript("1", lambda _: finish())
    QTimer.singleShot(int(a.seconds * 1000), done)

    def finish():
        print("\n===== 请求审计 =====")
        print(f"总请求 {len(HITS)}")
        missed = [h for h in HITS if not h[1]]
        failed = [h for h in missed if not h[3]]
        print(f"首次未命中 {len(missed)} 个（触发懒补漏）")
        print(f"懒补漏仍失败 {len(failed)} 个：")
        for path, _e, u, _a in failed[:40]:
            print(f"   ✗ {path}\n     -> {u}")
        print(f"\n懒补漏成功 {len([h for h in missed if h[3]])} 个：")
        for path, _e, _u, _a in [h for h in missed if h[3]][:15]:
            print(f"   ✓ {path}")
        srv.stop()
        app.quit()

    app.exec()


if __name__ == "__main__":
    main()
