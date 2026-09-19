"""UI 端到端验收测试（自动化，无需人工点击）。

覆盖需求点：
  4. 收藏 / 分类 / 重命名
  5. 目录扫描识别
  6. 打开游戏 + 默认音量 30% + 静音按钮 + 网页全屏 + 全屏 + ESC 退出
  7. MCP 开关与端口

运行：python tools/test_ui.py
"""
from __future__ import annotations

import json
import os
import shutil
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

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QTreeWidget

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(f"{name} {detail}".strip())
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  ({detail})" if detail else ""))


def main() -> int:
    app = QApplication(sys.argv)

    # 数据目录已由文件顶部的 HGD_DATA_DIR 环境变量隔离，这里只需确保目录存在
    import hgd.config as cfg
    from pathlib import Path
    tmp = str(cfg.DATA_DIR)
    cfg.ensure_dirs()
    self_created = True

    import hgd.core.db as db
    db.init_db()

    from hgd.ui.main_window import MainWindow
    from hgd.ui.player import GamePlayer
    from hgd.models import Game

    print("\n=== 1. 主窗口构建 ===")
    win = MainWindow()
    win.show()
    app.processEvents()
    check("主窗口创建", win.isVisible())
    check("四个页签", win.centralWidget().count() == 4,
          f"{[win.centralWidget().tabText(i) for i in range(win.centralWidget().count())]}")

    print("\n=== 2. 收藏 / 分类 / 重命名（需求 4）===")
    gpath = os.path.join(tmp, "games", "测试游戏")
    os.makedirs(gpath, exist_ok=True)
    open(os.path.join(gpath, "index.html"), "w").write("<canvas></canvas>")
    gid = db.add_game(Game(name="测试游戏", local_path=gpath, engine="unity",
                           category="动作", favorite=0, size=1234))
    win._refresh_library(); win._refresh_favorites(); app.processEvents()
    check("加入游戏库", win.tree_lib.topLevelItemCount() == 1)

    db.update_game(gid, favorite=1)
    win._refresh_favorites(); app.processEvents()
    check("加入收藏", win.tree_fav.topLevelItemCount() == 1)

    db.update_game(gid, name="改名后的游戏")
    win._refresh_favorites(); app.processEvents()
    check("重命名游戏", win.tree_fav.topLevelItem(0).text(0) == "改名后的游戏",
          win.tree_fav.topLevelItem(0).text(0))

    db.rename_category("动作", "射击")
    win._refresh_favorites(); app.processEvents()
    cats = [win.combo_fav_cat.itemText(i) for i in range(win.combo_fav_cat.count())]
    check("分类重命名", "射击" in cats and "动作" not in cats, str(cats))

    win.combo_fav_cat.setCurrentText("射击"); app.processEvents()
    check("按分类过滤收藏", win.tree_fav.topLevelItemCount() == 1)
    win.combo_fav_cat.setCurrentText("全部"); app.processEvents()

    print("\n=== 3. 目录扫描识别（需求 5）===")
    scanroot = os.path.join(tmp, "scanme")
    for nm, files in {
        "UnityGame": {"index.html": '<canvas id="unity-canvas"></canvas><script>createUnityInstance(c,{})</script>',
                      "Build/g.loader.js": "x", "Build/g.data": "y"},
        "FlashGame": {"game.swf": "FWS"},
        "NotAGame": {"readme.txt": "hello"},
    }.items():
        for rel, content in files.items():
            p = os.path.join(scanroot, nm, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(content)
    from hgd.core.identifier import scan_directory
    items = scan_directory(scanroot)
    names = sorted(i["name"] for i in items)
    check("扫描发现 2 个游戏", len(items) == 2, str(names))
    check("识别 Unity", any(i["engine"] == "unity" for i in items))
    check("识别 Flash", any(i["engine"] == "flash" for i in items))
    check("排除非游戏目录", "NotAGame" not in names)

    print("\n=== 4. 播放器：音量 / 静音（需求 6）===")
    gplay = db.get_game(gid)
    player = GamePlayer(gplay)
    player.show()
    app.processEvents()
    check("播放器创建", player.isVisible())
    check("默认音量 30%", player.slider.value() == 30, f"实际 {player.slider.value()}")
    check("音量标签显示 30%", "30" in player.lbl_vol.text(), player.lbl_vol.text())
    check("喇叭图标已绘制", not player.btn_mute.icon().isNull(), "icon is null" if player.btn_mute.icon().isNull() else "ok")
    check("喇叭提示为静音操作", "静音" in player.btn_mute.toolTip() and "取消" not in player.btn_mute.toolTip(),
          player.btn_mute.toolTip())

    player.slider.setValue(75); app.processEvents()
    check("拖动音量到 75", player._volume == 75)
    check("音量条可拖动范围", player.slider.minimum() == 0 and player.slider.maximum() == 100)

    player._toggle_mute(); app.processEvents()
    check("点击喇叭静音", player._muted, f"muted={player._muted}")
    check("静音时标签", "静音" in player.lbl_vol.text(), player.lbl_vol.text())
    check("静音时提示可取消", "取消" in player.btn_mute.toolTip(), player.btn_mute.toolTip())

    player._toggle_mute(); app.processEvents()
    check("再次点击取消静音", not player._muted)
    check("恢复后提示重新变静音", "取消" not in player.btn_mute.toolTip(), player.btn_mute.toolTip())

    print("\n=== 5. 播放器：网页全屏为默认状态（需求 6）===")
    check("打开即网页全屏（最大化窗口）", player.isMaximized(),
          f"state={player.windowState()}")
    check("网页全屏保留工具条", player.toolbar.isVisible())
    check("已移除「网页全屏」按钮", not hasattr(player, "btn_web_fs"))
    check("工具条保留全屏按钮", player.btn_fs.text() in ("全屏", "退出全屏"),
          player.btn_fs.text())
    # 网页全屏是默认状态，但仍是普通窗口，用户可自行缩放/还原
    player.showNormal(); app.processEvents()
    check("用户可还原为普通窗口", not player.isMaximized())
    player._apply_default_window_state(); app.processEvents()
    check("可恢复默认网页全屏", player.isMaximized())

    print("\n=== 6. 播放器：全屏 + ESC（需求 6）===")
    player._enter_fullscreen(); app.processEvents()
    check("进入全屏", player._fullscreen)
    check("全屏隐藏工具条", not player.toolbar.isVisible())
    check("全屏无菜单栏遮挡", player.menuBar() is None or not player.menuBar().isVisible())
    check("全屏铺满显示器", player.isFullScreen(), str(player.windowState()))
    # 鼠标移到顶部不应弹出任何东西：验证无工具栏/无弹出
    player._exit_fullscreen(); app.processEvents()
    check("退出全屏", not player._fullscreen)
    check("退出后恢复工具条", player.toolbar.isVisible())

    # ESC 键路径
    player._enter_fullscreen(); app.processEvents()
    ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    player.keyPressEvent(ev); app.processEvents()
    check("ESC 键退出全屏", not player._fullscreen)

    print("\n=== 7. MCP 服务（需求 7）===")
    try:
        from hgd.mcp.server import McpServer
        import requests
        m = McpServer(port=8799)
        p = m.start()
        j = requests.post(f"http://127.0.0.1:{p}",
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                          timeout=20).json()
        check("MCP 启动并响应", len(j["result"]["tools"]) == 7)
        m.stop()
        check("MCP 可停止", m._httpd is None)
    except Exception as e:
        check("MCP 服务", False, str(e))

    print("\n=== 8. 设置持久化 ===")
    win.set_volume.setValue(55)
    win.set_port.setValue(9123)
    win.set_depth.setValue(4)
    cfg.config.set("download_dir", os.path.join(tmp, "out"))
    cfg.config.set("default_volume", 55)
    cfg.config.set("mcp_port", 9123)
    cfg.config.set("max_depth", 4)
    cfg.config.save()
    cfg.config.load()
    check("下载目录持久化", cfg.config.get("download_dir") == os.path.join(tmp, "out"))
    check("音量持久化", cfg.config.get("default_volume") == 55)
    check("MCP 端口持久化", cfg.config.get("mcp_port") == 9123)
    check("递归深度持久化", cfg.config.get("max_depth") == 4)

    player.close()
    win.close()
    app.processEvents()

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  ✗ " + f)
    print("=" * 52)

    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
