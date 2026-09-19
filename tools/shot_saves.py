"""截图：重玩确认对话框与播放器工具条（视觉验收）。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HGD_DATA_DIR", os.path.join(
    os.environ.get("TEMP", "/tmp"), "hgd_shot_saves"))
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --log-level=3"
)
OUT = r"G:\traetool\temp\hgd-work\saves_gui"
os.makedirs(OUT, exist_ok=True)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QCheckBox, QPushButton

from hgd.models import Game
from hgd.ui.theme import app_style
from hgd.ui.player import GamePlayer


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(app_style())

    # 造一个有存档的游戏目录
    import tempfile
    root = tempfile.mkdtemp(prefix="hgd_shot_")
    gdir = os.path.join(root, "示例游戏")
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, "index.html"), "w", encoding="utf-8") as f:
        f.write("""<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#1a2332;color:#8fd;font:16px system-ui;display:flex;
align-items:center;justify-content:center;height:100vh">
<div style="text-align:center">
  <div style="font-size:34px;margin-bottom:10px">🎮 示例游戏</div>
  <div id="s" style="color:#5a7">存档进度加载中…</div>
</div>
<script>
var n = parseInt(localStorage.getItem('lvl') || '0', 10) + 1;
localStorage.setItem('lvl', String(n));
document.getElementById('s').textContent = '第 ' + n + ' 次打开本游戏（存档计数）';
</script>
</body></html>""")

    g = Game(id=9001, name="示例游戏", local_path=gdir,
             entry_file="index.html", engine="html", size=2_400_000)
    p = GamePlayer(g)
    p.resize(1000, 660)
    p.show()

    # 等页面加载
    loaded = [False]
    p.view.loadFinished.connect(lambda ok: loaded.__setitem__(0, True))
    for _ in range(80):
        app.processEvents(); time.sleep(0.1)
        if loaded[0]:
            break
    time.sleep(1.2)
    for _ in range(20):
        app.processEvents(); time.sleep(0.05)

    shot1 = os.path.join(OUT, "01_player_with_restart.png")
    p.grab().save(shot1)
    print("saved", shot1)

    # 拦截对话框：截图后再关闭
    holder = {}
    real_exec = QDialog.exec

    def shot_exec(self):
        holder["dlg"] = self
        self.show()
        for _ in range(25):
            app.processEvents(); time.sleep(0.06)
        shot2 = os.path.join(OUT, "02_restart_confirm.png")
        self.grab().save(shot2)
        print("saved", shot2)

        # 再截一张"已勾选"的状态
        chks = self.findChildren(QCheckBox)
        if chks:
            chks[0].setChecked(True)
            for _ in range(15):
                app.processEvents(); time.sleep(0.05)
            shot3 = os.path.join(OUT, "03_restart_confirm_checked.png")
            self.grab().save(shot3)
            print("saved", shot3)
        self.reject()
        return QDialog.Rejected

    QDialog.exec = shot_exec
    try:
        p._confirm_restart()
    finally:
        QDialog.exec = real_exec

    for _ in range(20):
        app.processEvents(); time.sleep(0.05)

    p.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
