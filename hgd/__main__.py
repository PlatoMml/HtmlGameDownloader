"""应用入口。"""
from __future__ import annotations

import argparse
import os
import sys

# 让 `python -m hgd` 与直接脚本运行都能工作
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "hgd"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .config import config, ensure_dirs
from .ui.theme import app_style


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="网页游戏下载器")
    parser.add_argument("--mcp", action="store_true", help="同时启动 MCP 服务")
    parser.add_argument("--mcp-port", type=int, default=0, help="MCP 端口")
    parser.add_argument("--no-gui", action="store_true", help="只运行 MCP，不显示界面")
    args = parser.parse_args(argv)

    ensure_dirs()

    # 清理上轮遗留的旧存档代际（重玩时因文件被占用未能立即删除的）
    try:
        from .core import saves
        n = saves.purge_all_pending()
        if n:
            print(f"[saves] 已清理 {n} 份过期存档")
    except Exception:
        pass

    # 高分屏支持（Qt6 默认已较好，这里显式兜底）
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("HtmlGameDownloader")
    app.setStyle("Fusion")
    app.setStyleSheet(app_style())

    mcp = None
    if args.mcp or args.no_gui or config.get("mcp_enabled"):
        from .mcp.server import McpServer
        port = args.mcp_port or int(config.get("mcp_port"))
        mcp = McpServer(host=config.get("mcp_host"), port=port)
        try:
            actual = mcp.start()
            print(f"[MCP] 已启动 http://{config.get('mcp_host')}:{actual}")
        except Exception as e:
            print(f"[MCP] 启动失败：{e}", file=sys.stderr)

    if args.no_gui:
        if not mcp:
            print("未启动 MCP，无事可做", file=sys.stderr)
            return 1
        try:
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    from .ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
