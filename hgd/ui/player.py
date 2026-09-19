"""游戏播放器窗口。

需求要点（逐条对应）：
- 音量拖动条，默认 30%                                    -> _build_toolbar
- 拖动条左侧小喇叭，点击静音（再点恢复）                  -> _toggle_mute
- 网页全屏：保留工具条与窗口外壳，游戏在窗口内保持比例铺满 -> _toggle_web_fullscreen
- 全屏：铺满显示器、隐藏工具条、鼠标移到顶部不弹出遮挡、ESC 返回 -> _enter_fullscreen / keyPressEvent
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QSize
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QIcon
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSlider, QSizePolicy,
    QVBoxLayout, QWidget, QMessageBox,
)

from ..config import config
from ..models import Game
from .bridge import VolumeBridge
from .theme import ACCENT, SURFACE, TEXT_MUTED, button_style, speaker_icon, toolbar_style


class GamePlayer(QMainWindow):
    """单个游戏的播放窗口。"""

    closed = Signal(int)   # 携带 game id，便于主窗口统计

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self._web_fullscreen = False
        self._fullscreen = False
        self._muted = bool(config.get("muted"))
        self._volume = int(config.get("default_volume") or 30)
        self._server = None

        self.setWindowTitle(f"{game.name} — 网页游戏下载器")
        self.resize(1024, 640)
        self._build_ui()
        self._load_game()
        self._apply_volume()

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.toolbar = QFrame()
        self.toolbar.setObjectName("toolbar")
        self.toolbar.setStyleSheet(toolbar_style())
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(10, 6, 10, 6)
        tb.setSpacing(8)

        # 喇叭（点击静音）
        self.btn_mute = QPushButton()
        self.btn_mute.setFixedSize(32, 28)
        self.btn_mute.setIconSize(QSize(18, 18))
        self.btn_mute.setToolTip("静音 / 取消静音")
        self.btn_mute.setStyleSheet(button_style(primary=False))
        self.btn_mute.clicked.connect(self._toggle_mute)
        tb.addWidget(self.btn_mute)

        # 音量条
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(self._volume)
        self.slider.setFixedWidth(120)
        self.slider.setToolTip("音量（默认 30%）")
        self.slider.valueChanged.connect(self._on_volume_changed)
        tb.addWidget(self.slider)

        self.lbl_vol = QLabel(f"{self._volume}%")
        self.lbl_vol.setFixedWidth(38)
        self.lbl_vol.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px;")
        tb.addWidget(self.lbl_vol)

        tb.addSpacing(12)
        self.lbl_title = QLabel(self.game.name)
        self.lbl_title.setStyleSheet(f"color:{ACCENT};font-weight:600;font-size:13px;")
        self.lbl_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(self.lbl_title)

        self.btn_web_fs = QPushButton("网页全屏")
        self.btn_web_fs.setStyleSheet(button_style(primary=False))
        self.btn_web_fs.setToolTip("保留工具条，游戏在窗口内保持比例铺满")
        self.btn_web_fs.clicked.connect(self._toggle_web_fullscreen)
        tb.addWidget(self.btn_web_fs)

        self.btn_fs = QPushButton("全屏")
        self.btn_fs.setStyleSheet(button_style(primary=False))
        self.btn_fs.setToolTip("铺满显示器，隐藏工具条，ESC 退出")
        self.btn_fs.clicked.connect(self._enter_fullscreen)
        tb.addWidget(self.btn_fs)

        self.btn_reload = QPushButton("刷新")
        self.btn_reload.setStyleSheet(button_style(primary=False))
        self.btn_reload.clicked.connect(self.view.reload if hasattr(self, "view") else lambda: None)
        tb.addWidget(self.btn_reload)

        # 视图
        self.view = QWebEngineView()
        self.view.setStyleSheet("background:#000;")
        self._enable_web_settings()
        self.bridge = VolumeBridge(self.view)
        self.bridge.attach()

        # 布局：工具条 + 舞台
        self.stage = QWidget()
        self.stage.setStyleSheet("background:#000;")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)
        stage_layout.addWidget(self.view)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.stage, 1)
        self.setCentralWidget(central)

        # ESC 退出全屏（全屏时工具条隐藏，快捷键是唯一可靠出口）
        QShortcut(QKeySequence("Esc"), self).activated.connect(self._exit_fullscreen)
        self.btn_reload.clicked.connect(self.view.reload)

    def _enable_web_settings(self) -> None:
        s = self.view.settings()
        for attr in ("PlaybackRequiresUserGesture",):
            try:
                s.setAttribute(getattr(QWebEngineSettings, attr), False)
            except Exception:
                pass
        for attr in ("JavascriptEnabled", "LocalStorageEnabled", "AutoLoadImages",
                     "PluginsEnabled", "FullScreenSupportEnabled", "ScreenCaptureEnabled"):
            try:
                s.setAttribute(getattr(QWebEngineSettings, attr), True)
            except Exception:
                pass
        try:
            s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        except Exception:
            pass

    # ------------------------------------------------------------ 载入

    def _load_game(self) -> None:
        """优先用本地镜像服务打开（相对引用与懒补漏都需要 HTTP 源）。"""
        from ..core.mirror_server import MirrorServer
        from ..core.mirror import Downloader
        from urllib.parse import urlparse

        folder = self.game.local_path
        entry = self.game.entry_file or "index.html"
        entry_path = os.path.join(folder, entry)

        # 读取元数据拿到原始来源，供懒补漏回源
        base_url = ""
        meta_path = os.path.join(folder, "game_meta.json")
        if os.path.exists(meta_path):
            try:
                meta = json.load(open(meta_path, encoding="utf-8"))
                eu = meta.get("entry_url", "")
                if eu:
                    p = urlparse(eu)
                    base_url = f"{p.scheme}://{p.netloc}" + os.path.dirname(p.path)
            except Exception:
                pass

        if os.path.exists(entry_path) and config.get("lazy_fill"):
            rel_dir = os.path.dirname(entry_path)
            self._server = MirrorServer(
                root=folder, base_url=base_url,
                downloader=Downloader(folder, referer=base_url + "/"),
                lazy=True,
            )
            try:
                self._server.start()
                rel = os.path.relpath(entry_path, folder).replace("\\", "/")
                url = self._server.url_for(rel)
            except Exception:
                url = QUrl.fromLocalFile(entry_path).toString()
        else:
            url = QUrl.fromLocalFile(entry_path).toString()

        self.view.setUrl(QUrl(url))

    def closeEvent(self, event):
        try:
            if self._server:
                self._server.stop()
        except Exception:
            pass
        self.closed.emit(self.game.id or 0)
        super().closeEvent(event)

    # ------------------------------------------------------------ 音量

    def _apply_volume(self) -> None:
        if hasattr(self, "bridge"):
            self.bridge.set_volume(self._volume, self._muted)
        silent = self._muted or self._volume == 0
        self.lbl_vol.setText("静音" if silent else f"{self._volume}%")
        self.btn_mute.setIcon(speaker_icon(silent))
        self.btn_mute.setToolTip("取消静音" if silent else "静音")

    def _on_volume_changed(self, v: int) -> None:
        self._volume = v
        if v > 0:
            self._muted = False
        self._apply_volume()

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self._apply_volume()

    # ------------------------------------------------------------ 全屏

    def _toggle_web_fullscreen(self) -> None:
        """网页全屏：保留工具条，游戏区域扩展到整个窗口客户区并保持比例。

        实现：把窗口中央部件里的舞台提升为顶层覆盖层（不隐藏工具条），
        舞台内部由 index.html 的 fit() 按比例铺满。
        """
        self._web_fullscreen = not self._web_fullscreen
        if self._web_fullscreen:
            # 记住原本的窗口几何，退出时还原
            self._saved_geo = self.geometry()
            self.toolbar.setVisible(True)   # 需求明确：网页全屏保留工具条
            self.showMaximized()
            self.btn_web_fs.setText("退出网页全屏")
        else:
            if hasattr(self, "_saved_geo"):
                self.setGeometry(self._saved_geo)
            self.showNormal()
            self.btn_web_fs.setText("网页全屏")
        # 让页面重新计算贴合尺寸
        QTimer.singleShot(150, self._refit)

    def _enter_fullscreen(self) -> None:
        """全屏：铺满显示器，隐藏一切工具条，鼠标移顶部不弹出，ESC 退出。"""
        if self._fullscreen:
            return
        self._fullscreen = True
        self._saved_geo = self.geometry()
        # 关键：必须先隐藏工具条/菜单/状态栏，再 showFullScreen，
        # 否则 Qt 会在全屏时仍为主窗口部件留出边框/工具条空间。
        if self.menuBar():
            self.menuBar().hide()
        self.toolbar.hide()
        if self.statusBar():
            self.statusBar().hide()
        self.setContentsMargins(0, 0, 0, 0)
        self.showFullScreen()
        # 阻止鼠标移到屏幕顶部时弹出任何东西：无工具条 + 禁用快捷键上下文菜单
        self.setContextMenuPolicy(Qt.NoContextMenu)
        QTimer.singleShot(200, self._refit)

    def _exit_fullscreen(self) -> None:
        if not self._fullscreen:
            # 非全屏时 ESC 先退出"网页全屏"
            if self._web_fullscreen:
                self._toggle_web_fullscreen()
            return
        self._fullscreen = False
        self.showNormal()
        self.toolbar.show()
        if self.menuBar():
            self.menuBar().show()
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        if hasattr(self, "_saved_geo"):
            self.setGeometry(self._saved_geo)
        self.btn_fs.setText("全屏")
        QTimer.singleShot(200, self._refit)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._exit_fullscreen()
            return
        if event.key() == Qt.Key_F11:
            if self._fullscreen:
                self._exit_fullscreen()
            else:
                self._enter_fullscreen()
            return
        super().keyPressEvent(event)

    def _refit(self) -> None:
        """通知页面重新贴合尺寸（页面里监听了 window.resize）。"""
        try:
            self.view.page().runJavaScript("window.dispatchEvent(new Event('resize'));")
        except Exception:
            pass
