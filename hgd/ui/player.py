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
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSlider, QSizePolicy,
    QVBoxLayout, QWidget, QMessageBox, QStatusBar, QDialog, QCheckBox,
)

from ..config import config
from ..models import Game
from .bridge import VolumeBridge
from .theme import (
    ACCENT, SURFACE, TEXT, TEXT_MUTED, button_style, speaker_icon, toolbar_style,
)


def _human_size(n: int) -> str:
    """字节数转可读文本。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


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
        self._profile = None
        self._saved_geo = None

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

        self.btn_restart = QPushButton("重玩")
        self.btn_restart.setStyleSheet(button_style(primary=False))
        self.btn_restart.setToolTip("清除本游戏的存档，从头开始")
        self.btn_restart.clicked.connect(self._confirm_restart)
        tb.addWidget(self.btn_restart)

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

        # 状态栏：显示"存档已重置"之类的操作反馈
        self.setStatusBar(QStatusBar())
        self.statusBar().setStyleSheet(
            f"QStatusBar{{background:{SURFACE};color:{TEXT_MUTED};font-size:12px;}}")

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
        """载入游戏。

        使用「每游戏独立的持久化 profile + 稳定端口」，让存档能跨会话保留：
        - 端口稳定 -> 源不变 -> 浏览器能找回该游戏的旧存档
        - 独立 profile -> 不同游戏的存档互不干扰
        """
        from ..core import saves
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
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
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
                # 固定端口：这是存档能在下次打开时被读到的前提
                port = saves.port_for(self.game)
                self._server.start(preferred=port)
                rel = os.path.relpath(entry_path, folder).replace("\\", "/")
                url = self._server.url_for(rel)
            except Exception:
                url = QUrl.fromLocalFile(entry_path).toString()
        else:
            url = QUrl.fromLocalFile(entry_path).toString()

        # 持久化 profile：让 localStorage / IndexedDB / Cookie 落到磁盘
        self._setup_profile()
        self.view.setUrl(QUrl(url))

    def _setup_profile(self) -> None:
        """为该游戏建立独立的持久化存储 profile。

        存储目录 = data/saves/<游戏标识>/gen<N>/
        每个游戏一份，互不干扰；重玩时切换到 gen<N+1> 拿到全新存档。
        """
        from ..core import saves
        from PySide6.QtWebEngineCore import QWebEngineProfile

        try:
            # 顺带清理上一轮遗留的旧代际（句柄此时应已释放）
            try:
                saves.purge_pending(self.game)
            except Exception:
                pass

            sdir = saves.save_dir(self.game)
            sdir.mkdir(parents=True, exist_ok=True)
            profile = QWebEngineProfile(saves.profile_name(self.game), self)
            profile.setPersistentStoragePath(str(sdir))
            profile.setCachePath(str(sdir / "_cache"))
            profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            self._profile = profile
            self.view.setPage(QWebEnginePage(profile, self.view))
            self._enable_web_settings()
        except Exception:
            # profile 建立失败不应阻断游玩，退回默认页面
            self._profile = None

    def _teardown_profile(self) -> None:
        """释放 profile 与其页面，确保 leveldb 句柄被关闭。

        清档前必须调用：否则文件被占用，删除会失败或残留脏数据。
        """
        try:
            if self.view.page():
                self.view.setPage(None)
        except Exception:
            pass
        try:
            if self._profile:
                self._profile.deleteLater()
        except Exception:
            pass
        self._profile = None

    def _restart_fresh(self) -> None:
        """重玩：切换到全新存档代际后重新载入游戏。"""
        from ..core import saves

        # 1) 先释放 profile 与页面，减少对旧存档目录的占用
        self._teardown_profile()
        if self._server:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None

        # 2) 切换存档代际（不直接删正在使用的目录，避免文件锁与半删状态）
        ok, msg = saves.clear_save(self.game)

        # 3) 稍作延迟让 Qt 回收资源，再以新代际载入
        QTimer.singleShot(220, lambda: self._after_clear(ok, msg))

    def _after_clear(self, ok: bool, msg: str) -> None:
        self._load_game()
        self._apply_volume()
        if ok:
            self.statusBar().showMessage("存档已重置，已从新的存档开始", 6000)
        else:
            QMessageBox.warning(self, "重玩失败", msg)

    def closeEvent(self, event):
        try:
            if self._server:
                self._server.stop()
        except Exception:
            pass
        # 释放 profile，让存档内容完整落盘
        try:
            if self.view.page():
                self.view.setPage(None)
        except Exception:
            pass
        try:
            if self._profile:
                self._profile.deleteLater()
        except Exception:
            pass
        self.closed.emit(self.game.id or 0)
        super().closeEvent(event)

    # ------------------------------------------------------------ 重玩

    def _confirm_restart(self) -> None:
        """重玩：二次确认后清除本游戏存档。

        采用「勾选确认」式二次确认：必须先勾选"我已知晓"，
        "清除存档并重玩"按钮才会启用。比连弹两个对话框清晰，
        也不会让人能一路回车误删进度。
        """
        from ..core import saves

        has_save = saves.save_exists(self.game)
        size = saves.save_size(self.game)

        dlg = QDialog(self)
        dlg.setWindowTitle("重玩")
        dlg.setMinimumWidth(430)
        dlg.setStyleSheet(f"""
            QDialog {{ background:{SURFACE}; }}
            QLabel {{ color:{TEXT}; }}
            QCheckBox {{ color:{TEXT}; }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        title = QLabel(f"要重新开始《{self.game.name}》吗？")
        title.setStyleSheet(f"color:{ACCENT};font-size:15px;font-weight:600;")
        lay.addWidget(title)

        if has_save:
            desc = (f"将清除本游戏的全部存档数据（约 {_human_size(size)}），"
                    "包括游戏进度、设置与缓存。\n\n"
                    "清除后无法恢复，游戏会从全新的存档开始。")
        else:
            desc = ("当前没有检测到存档数据。\n\n"
                    "继续会重新载入游戏，并清除可能存在的临时数据。")
        body = QLabel(desc)
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{TEXT_MUTED};font-size:13px;")
        lay.addWidget(body)

        chk = QCheckBox("我已了解进度将永久丢失")
        lay.addWidget(chk)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(button_style(primary=False))
        btn_cancel.clicked.connect(dlg.reject)
        row.addWidget(btn_cancel)

        btn_ok = QPushButton("清除存档并重玩")
        btn_ok.setStyleSheet(button_style())
        btn_ok.setEnabled(False)          # 必须先勾选
        btn_ok.clicked.connect(dlg.accept)
        row.addWidget(btn_ok)
        lay.addLayout(row)

        chk.toggled.connect(btn_ok.setEnabled)
        btn_cancel.setDefault(True)

        if dlg.exec() != QDialog.Accepted or not chk.isChecked():
            return

        self.statusBar().showMessage("正在重置存档…")
        QTimer.singleShot(60, self._restart_fresh)

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
