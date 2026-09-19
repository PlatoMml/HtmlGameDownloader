"""GUI 真机启动测试：启动主窗口与播放器窗口，截图确认渲染正常。

这是"软件真的能用"的最终确认——无头测试测的是逻辑，这里测的是真实窗口。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# 隔离数据目录：测试绝不写入用户的真实游戏库
os.environ.setdefault("HGD_DATA_DIR",
                      os.path.join(tempfile.gettempdir(), "hgd_testdata_" + str(os.getpid())))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader "
    "--autoplay-policy=no-user-gesture-required --log-level=3"
)
OUT = r"G:\traetool\temp\hgd-work\gui"
os.makedirs(OUT, exist_ok=True)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from hgd.ui.main_window import MainWindow
from hgd.models import Game
from hgd.core import db


def shot(widget, name: str):
    p = os.path.join(OUT, name)
    widget.grab().save(p)
    print(f"  saved {p}")
    return p


def main() -> int:
    app = QApplication(sys.argv)
    from hgd.ui.theme import app_style
    app.setStyle("Fusion")
    app.setStyleSheet(app_style())

    print("=== 主窗口 ===")
    win = MainWindow()
    win.resize(1180, 740)
    win.show()
    app.processEvents()
    time.sleep(0.6)
    app.processEvents()
    shot(win, "01_main_download.png")

    # 切到各页签截图
    tabs = win.centralWidget()
    for i, nm in [(1, "02_library"), (2, "03_favorites"), (3, "04_settings")]:
        tabs.setCurrentIndex(i)
        app.processEvents(); time.sleep(0.35); app.processEvents()
        shot(win, f"{nm}.png")
    tabs.setCurrentIndex(0)
    app.processEvents()

    print("=== 播放器：Unity 游戏 ===")
    gp = r"G:\traetool\temp\hgd-work\final_test\数据之翼"
    if os.path.isdir(gp):
        g = Game(id=1, name="数据之翼", local_path=gp, entry_file="index.html", engine="unity")
        from hgd.ui.player import GamePlayer
        p = GamePlayer(g)
        p.resize(1024, 700); p.show()
        app.processEvents()
        # 等 Unity 起画面
        for _ in range(16):
            app.processEvents(); time.sleep(0.5)
        shot(p, "05_player_unity.png")

        print("  -- 测试网页全屏 --")
        p._toggle_web_fullscreen(); app.processEvents(); time.sleep(0.8); app.processEvents()
        shot(p, "06_player_web_fullscreen.png")
        print(f"     toolbar visible = {p.toolbar.isVisible()}")
        p._toggle_web_fullscreen(); app.processEvents(); time.sleep(0.4)

        print("  -- 测试全屏 --")
        p._enter_fullscreen(); app.processEvents(); time.sleep(0.9); app.processEvents()
        shot(p, "07_player_fullscreen.png")
        print(f"     toolbar visible = {p.toolbar.isVisible()} (应为 False)")
        p._exit_fullscreen(); app.processEvents(); time.sleep(0.5)

        print("  -- 测试静音按钮 --")
        p._toggle_mute(); app.processEvents(); time.sleep(0.3); app.processEvents()
        shot(p, "08_player_muted.png")
        p.close()

    print("=== 播放器：Flash 游戏 ===")
    fp = r"G:\traetool\temp\hgd-work\flash_real\救援棕色大象"
    if os.path.isdir(fp):
        g2 = Game(id=2, name="救援棕色大象", local_path=fp, entry_file="index.html", engine="flash")
        from hgd.ui.player import GamePlayer
        p2 = GamePlayer(g2)
        p2.resize(1024, 700); p2.show()
        for _ in range(8):
            app.processEvents(); time.sleep(0.5)
        shot(p2, "09_player_flash.png")
        p2.close()

    win.close()
    print("GUI 测试完成，截图目录:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
