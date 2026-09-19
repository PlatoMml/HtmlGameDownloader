"""主窗口。

刻意简化的点（对标参考仓库的繁琐）：
- 原版：选站点 -> 填 URL -> 分析 -> 手动确认路径 -> 填文件名 -> 关联下载 -> 再手动转 htm
- 本版：贴 URL -> 自动识别（名字可改）-> 选目录 -> 下载。其余全自动化。
"""
from __future__ import annotations

import os
import time
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QDialog, QDialogButtonBox,
    QCheckBox, QSpinBox, QFormLayout,
)

from ..config import config
from ..core.db import (
    add_game, categories, get_game, init_db, list_games, rename_category,
    update_game, delete_game, is_available, find_missing, prune_missing,
)
from ..core.downloader import GameDownloader
from ..core.identifier import dir_size, scan_directory
from ..models import DownloadProgress, Game
from .player import GamePlayer
from .theme import (
    ACCENT, BG, SURFACE, SURFACE_2, TEXT, TEXT_MUTED, app_style, button_style,
    combo_style, input_style, list_style,
)


# ---------------------------------------------------------------- 工作线程

class _DownloadWorker(QThread):
    sig = Signal(object)          # DownloadProgress
    done = Signal(object)         # Game or None

    def __init__(self, url: str, name: str, dest: str, category: str, favorite: int):
        super().__init__()
        self.url, self.name, self.dest = url, name, dest
        self.category, self.favorite = category, favorite
        self._dl: Optional[GameDownloader] = None

    def run(self):
        self._dl = GameDownloader(lambda p: self.sig.emit(p))
        g = self._dl.download(self.url, self.name, self.dest,
                              self.category, self.favorite)
        self.done.emit(g)

    def cancel(self):
        if self._dl:
            self._dl.cancel()


class _IdentifyWorker(QThread):
    done = Signal(object)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        from ..core.identifier import identify_url
        try:
            self.done.emit(identify_url(self.url))
        except Exception as e:
            from ..models import IdentifyResult
            self.done.emit(IdentifyResult(False, url=self.url, message=str(e)))


class _ScanWorker(QThread):
    done = Signal(object)

    def __init__(self, directory: str):
        super().__init__()
        self.directory = directory

    def run(self):
        self.done.emit(scan_directory(self.directory))


# ---------------------------------------------------------------- 主窗口

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        init_db()
        self.setWindowTitle("网页游戏下载器  ·  HTML / Flash / Unity")
        self.resize(1180, 740)
        self._workers: List[QThread] = []
        self._players: List[GamePlayer] = []
        self.setStyleSheet(app_style())
        self._build_ui()
        self._refresh_library()
        self._refresh_favorites()

    # ============================================================ UI

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        tabs.addTab(self._build_download_tab(), "下载")
        tabs.addTab(self._build_library_tab(), "游戏库")
        tabs.addTab(self._build_fav_tab(), "收藏夹")
        tabs.addTab(self._build_settings_tab(), "设置")

        self.statusBar().showMessage("就绪")
        self._mcp_server = None

    # -------------------------------------------------- 下载页

    def _build_download_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(14)

        hint = QLabel("把游戏网址粘贴进来 —— 自动识别名称和类型，可直接修改")
        hint.setStyleSheet(f"color:{TEXT_MUTED};font-size:13px;")
        lay.addWidget(hint)

        row = QHBoxLayout()
        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText("例如 https://www.4399.com/flash/262895_3.htm")
        self.edit_url.setStyleSheet(input_style())
        self.edit_url.returnPressed.connect(self._on_identify)
        row.addWidget(self.edit_url, 1)
        self.btn_identify = QPushButton("识别")
        self.btn_identify.setStyleSheet(button_style())
        self.btn_identify.clicked.connect(self._on_identify)
        row.addWidget(self.btn_identify)
        lay.addLayout(row)

        # 识别结果区
        box = QFrame()
        box.setStyleSheet(f"QFrame{{background:{SURFACE};border:1px solid #2a2f3a;border-radius:10px;}}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setSpacing(10)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("游戏名"))
        self.edit_name = QLineEdit()
        self.edit_name.setStyleSheet(input_style())
        self.edit_name.setPlaceholderText("识别后自动填入，可手动修改")
        r1.addWidget(self.edit_name, 1)
        bl.addLayout(r1)

        r2 = QHBoxLayout()
        self.lbl_type = QLabel("类型：—")
        self.lbl_type.setStyleSheet(f"color:{ACCENT};font-size:12px;")
        r2.addWidget(self.lbl_type)
        self.lbl_entry = QLabel("")
        self.lbl_entry.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:11px;background:transparent;border:0;")
        self.lbl_entry.setTextInteractionFlags(Qt.TextSelectableByMouse)
        r2.addWidget(self.lbl_entry, 1)
        bl.addLayout(r2)

        # 目录选择
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("保存到"))
        self.edit_dir = QLineEdit(config.get("download_dir"))
        self.edit_dir.setStyleSheet(input_style())
        r3.addWidget(self.edit_dir, 1)
        self.btn_dir = QPushButton("选择目录")
        self.btn_dir.setStyleSheet(button_style(primary=False))
        self.btn_dir.clicked.connect(self._pick_dir)
        r3.addWidget(self.btn_dir)
        bl.addLayout(r3)

        # 分类 + 收藏
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("分类"))
        self.combo_cat = QComboBox()
        self.combo_cat.setEditable(True)
        self.combo_cat.setStyleSheet(combo_style())
        self.combo_cat.addItems(["未分类", "动作", "益智", "射击", "体育", "休闲"])
        r4.addWidget(self.combo_cat)
        self.chk_fav = QCheckBox("加入收藏")
        r4.addWidget(self.chk_fav)
        r4.addStretch(1)
        bl.addLayout(r4)
        lay.addWidget(box)

        # 按钮行
        r5 = QHBoxLayout()
        self.btn_download = QPushButton("下载游戏")
        self.btn_download.setStyleSheet(button_style())
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._on_download)
        r5.addWidget(self.btn_download)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setStyleSheet(button_style(primary=False))
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        r5.addWidget(self.btn_cancel)
        r5.addStretch(1)
        lay.addLayout(r5)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximum(0)
        lay.addWidget(self.progress)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px;")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        # 批量导入
        sep = QLabel("—— 或者 ——")
        sep.setAlignment(Qt.AlignCenter)
        sep.setStyleSheet(f"color:#5a6472;font-size:12px;")
        lay.addWidget(sep)
        rb = QHBoxLayout()
        rb.addWidget(QLabel("本地已有游戏？扫描目录自动登记："))
        self.btn_scan_dir = QPushButton("扫描目录")
        self.btn_scan_dir.setStyleSheet(button_style(primary=False))
        self.btn_scan_dir.clicked.connect(self._on_scan_dir)
        rb.addWidget(self.btn_scan_dir)
        rb.addStretch(1)
        lay.addLayout(rb)

        lay.addStretch(1)
        self._ident_result = None
        return w

    # -------------------------------------------------- 游戏库 / 收藏夹

    def _build_library_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索游戏名…")
        self.edit_search.setStyleSheet(input_style())
        self.edit_search.textChanged.connect(self._refresh_library)
        top.addWidget(self.edit_search, 1)
        btn_del = QPushButton("删除")
        btn_del.setStyleSheet(button_style(primary=False))
        btn_del.clicked.connect(lambda: self._delete_selected(self.tree_lib))
        top.addWidget(btn_del)
        btn_open = QPushButton("打开文件夹")
        btn_open.setStyleSheet(button_style(primary=False))
        btn_open.clicked.connect(lambda: self._open_folder(self.tree_lib))
        top.addWidget(btn_open)
        self.btn_clean = QPushButton("清理失效记录")
        self.btn_clean.setStyleSheet(button_style(primary=False))
        self.btn_clean.setToolTip("移除本地文件已不存在的游戏记录")
        self.btn_clean.clicked.connect(self._clean_missing)
        top.addWidget(self.btn_clean)
        lay.addLayout(top)

        self.tree_lib = self._make_game_tree()
        lay.addWidget(self.tree_lib, 1)
        lay.addWidget(QLabel("双击行即可开始游戏"))
        return w

    def _build_fav_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.combo_fav_cat = QComboBox()
        self.combo_fav_cat.setStyleSheet(combo_style())
        self.combo_fav_cat.currentTextChanged.connect(self._refresh_favorites)
        top.addWidget(self.combo_fav_cat)
        btn_new_cat = QPushButton("新建分类")
        btn_new_cat.setStyleSheet(button_style(primary=False))
        btn_new_cat.clicked.connect(self._new_category)
        top.addWidget(btn_new_cat)
        btn_ren_cat = QPushButton("重命名分类")
        btn_ren_cat.setStyleSheet(button_style(primary=False))
        btn_ren_cat.clicked.connect(self._rename_category)
        top.addWidget(btn_ren_cat)
        top.addStretch(1)
        btn_rename = QPushButton("重命名游戏")
        btn_rename.setStyleSheet(button_style(primary=False))
        btn_rename.clicked.connect(self._rename_game)
        top.addWidget(btn_rename)
        btn_unfav = QPushButton("取消收藏")
        btn_unfav.setStyleSheet(button_style(primary=False))
        btn_unfav.clicked.connect(self._unfavorite)
        top.addWidget(btn_unfav)
        lay.addLayout(top)

        self.tree_fav = self._make_game_tree()
        lay.addWidget(self.tree_fav, 1)
        lay.addWidget(QLabel("收藏夹支持按分类整理与重命名 —— 双击开始游戏"))
        return w

    def _make_game_tree(self) -> QTreeWidget:
        t = QTreeWidget()
        t.setStyleSheet(list_style())
        t.setHeaderLabels(["游戏名", "类型", "分类", "大小", "来源"])
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = t.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        t.itemDoubleClicked.connect(self._play_item)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(
            lambda pos, tree=t: self._tree_menu(pos, tree))
        return t

    # -------------------------------------------------- 设置页

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)

        self.set_dir = QLineEdit(config.get("download_dir"))
        self.set_dir.setStyleSheet(input_style())
        btn = QPushButton("浏览")
        btn.setStyleSheet(button_style(primary=False))
        btn.clicked.connect(lambda: self._pick_dir_for(self.set_dir))
        row = QHBoxLayout()
        row.addWidget(self.set_dir, 1)
        row.addWidget(btn)
        rw = QWidget()
        rw.setLayout(row)
        form.addRow("默认下载目录", rw)

        self.set_proxy = QLineEdit(config.get("proxy"))
        self.set_proxy.setPlaceholderText("留空则直连，如 http://127.0.0.1:10808")
        self.set_proxy.setStyleSheet(input_style())
        form.addRow("代理", self.set_proxy)

        self.set_volume = QSpinBox()
        self.set_volume.setRange(0, 100)
        self.set_volume.setValue(int(config.get("default_volume")))
        form.addRow("默认音量 (%)", self.set_volume)

        self.set_mcp = QCheckBox("启用 MCP 服务")
        self.set_mcp.setChecked(bool(config.get("mcp_enabled")))
        form.addRow("", self.set_mcp)

        self.set_port = QSpinBox()
        self.set_port.setRange(1024, 65535)
        self.set_port.setValue(int(config.get("mcp_port")))
        form.addRow("MCP 端口", self.set_port)

        self.set_lazy = QCheckBox("运行时自动补全缺失资源（推荐）")
        self.set_lazy.setChecked(bool(config.get("lazy_fill")))
        form.addRow("", self.set_lazy)

        self.set_depth = QSpinBox()
        self.set_depth.setRange(1, 6)
        self.set_depth.setValue(int(config.get("max_depth")))
        form.addRow("递归深度", self.set_depth)
        lay.addLayout(form)

        row2 = QHBoxLayout()
        b_save = QPushButton("保存设置")
        b_save.setStyleSheet(button_style())
        b_save.clicked.connect(self._save_settings)
        row2.addWidget(b_save)
        self.lbl_mcp = QLabel("")
        self.lbl_mcp.setStyleSheet(f"color:{ACCENT};font-size:12px;")
        row2.addWidget(self.lbl_mcp)
        row2.addStretch(1)
        lay.addLayout(row2)

        info = QLabel(
            "MCP：启用后外部 Agent 可通过该端口调用下载能力，处理疑难游戏。\n"
            "技能文档见项目根目录 skill/SKILL.md。"
        )
        info.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px;")
        lay.addWidget(info)
        lay.addStretch(1)
        return w

    # ============================================================ 交互

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.edit_dir.text())
        if d:
            self.edit_dir.setText(d)

    def _pick_dir_for(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        if d:
            edit.setText(d)

    # -------------------------------------------------- 识别

    def _on_identify(self) -> None:
        url = self.edit_url.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "请先填写游戏网址")
            return
        self.btn_identify.setEnabled(False)
        self.lbl_status.setText("正在识别…")
        self._ident_result = None
        self.btn_download.setEnabled(False)

        worker = _IdentifyWorker(url)
        worker.done.connect(self._on_identified)
        worker.start()
        self._workers.append(worker)

    def _on_identified(self, res) -> None:
        self.btn_identify.setEnabled(True)
        if not res or not res.ok:
            self.lbl_status.setText(f"识别失败：{getattr(res, 'message', '未知错误')}")
            self.lbl_type.setText("类型：—")
            return
        self._ident_result = res
        self.edit_name.setText(res.name)
        label = {"flash": "Flash (Ruffle)", "unity": "Unity WebGL", "html": "HTML5"}.get(
            res.engine, res.engine or "未知")
        size = f"{res.width}×{res.height}" if res.width and res.height else ""
        self.lbl_type.setText(f"类型：{label}  {size}")
        self.lbl_entry.setText(f"入口：{res.entry_url}")
        self.btn_download.setEnabled(True)
        self.lbl_status.setText("识别完成，确认名称后点「下载游戏」")

    # -------------------------------------------------- 下载

    def _on_download(self) -> None:
        if not self._ident_result:
            return
        url = self.edit_url.text().strip()
        name = self.edit_name.text().strip() or self._ident_result.name
        dest = self.edit_dir.text().strip() or config.get("download_dir")
        if not dest:
            QMessageBox.information(self, "提示", "请选择保存目录")
            return
        os.makedirs(dest, exist_ok=True)
        cat = self.combo_cat.currentText().strip() or "未分类"
        fav = 1 if self.chk_fav.isChecked() else 0

        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setVisible(True)
        self.lbl_status.setText("开始下载…")

        worker = _DownloadWorker(url, name, dest, cat, fav)
        worker.sig.connect(self._on_progress)
        worker.done.connect(self._on_downloaded)
        worker.start()
        self._workers.append(worker)
        self._current_worker = worker

    def _on_progress(self, p: DownloadProgress) -> None:
        phase = {"analyse": "识别", "download": "下载入口", "mirror": "抓取资源",
                 "package": "打包", "done": "完成", "error": "出错"}.get(p.phase, p.phase)
        msg = p.message or p.current
        if p.done:
            self.lbl_status.setText(f"{phase}：{msg}（已处理 {p.done} 个）")
        else:
            self.lbl_status.setText(f"{phase}：{msg}" if msg else phase)

    def _on_downloaded(self, g: Optional[Game]) -> None:
        self.progress.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_download.setEnabled(True)
        if not g:
            self.lbl_status.setText("下载失败，请检查网址/网络/代理设置")
            QMessageBox.warning(self, "下载失败", "未能完成下载，请查看状态栏提示。")
            return
        size_mb = g.size / 1024 / 1024
        self.lbl_status.setText(f"✓ 已保存：{g.name}（{size_mb:.1f} MB）→ {g.local_path}")
        self._refresh_library()
        self._refresh_favorites()
        QMessageBox.information(
            self, "完成",
            f"《{g.name}》下载完成\n\n位置：{g.local_path}\n大小：{size_mb:.1f} MB"
        )

    def _on_cancel(self) -> None:
        if hasattr(self, "_current_worker") and self._current_worker:
            try:
                self._current_worker.cancel()
            except Exception:
                pass
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("已请求取消…")

    # -------------------------------------------------- 目录扫描

    def _on_scan_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择包含游戏的目录", config.get("last_scan_dir") or "")
        if not d:
            return
        config.set("last_scan_dir", d)
        self.lbl_status.setText(f"扫描目录：{d} …")
        w = _ScanWorker(d)
        w.done.connect(self._on_scanned)
        w.start()
        self._workers.append(w)

    def _on_scanned(self, items) -> None:
        if not items:
            self.lbl_status.setText("未在该目录发现游戏")
            QMessageBox.information(self, "扫描结果", "未发现可识别的游戏。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"发现 {len(items)} 个游戏")
        dlg.resize(640, 400)
        dlg.setStyleSheet(app_style())
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("勾选要登记到游戏库的游戏："))
        tree = QTreeWidget()
        tree.setStyleSheet(list_style())
        tree.setHeaderLabels(["游戏名", "类型", "入口", "大小"])
        tree.setRootIsDecorated(False)
        for it in items:
            node = QTreeWidgetItem([
                it["name"],
                {"unity": "Unity", "flash": "Flash", "html": "HTML5"}.get(it["engine"], it["engine"]),
                it["entry"], f"{it['size']/1024/1024:.1f} MB"])
            node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
            node.setCheckState(0, Qt.Checked)
            tree.addTopLevelItem(node)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(tree, 1)
        row = QHBoxLayout()
        cat_edit = QComboBox()
        cat_edit.setEditable(True)
        cat_edit.setStyleSheet(combo_style())
        cat_edit.addItems(["未分类", "动作", "益智", "射击", "体育", "休闲"])
        row.addWidget(QLabel("分类"))
        row.addWidget(cat_edit)
        row.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("加入游戏库")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        row.addWidget(btns)
        lay.addLayout(row)

        if dlg.exec() != QDialog.Accepted:
            return
        n = 0
        cat = cat_edit.currentText().strip() or "未分类"
        for i, node in enumerate(
                [tree.topLevelItem(k) for k in range(tree.topLevelItemCount())]):
            if node.checkState(0) != Qt.Checked:
                continue
            it = items[i]
            g = Game(name=it["name"], local_path=it["path"], entry_file=it["entry"],
                     engine=it["engine"], category=cat, size=it["size"])
            add_game(g)
            n += 1
        self.statusBar().showMessage(f"已登记 {n} 个游戏")
        self.lbl_status.setText(f"✓ 已登记 {n} 个游戏")
        self._refresh_library()
        self._refresh_favorites()

    # -------------------------------------------------- 列表

    def _fill_tree(self, tree: QTreeWidget, games: List[Game]) -> None:
        tree.clear()
        for g in games:
            size_mb = g.size / 1024 / 1024 if g.size else 0.0
            exists = is_available(g)
            label = g.name if exists else f"{g.name}（文件已丢失）"
            node = QTreeWidgetItem([
                label,
                {"unity": "Unity", "flash": "Flash", "html": "HTML5"}.get(g.engine, g.engine or "-"),
                g.category, f"{size_mb:.1f} MB" if size_mb else "-",
                g.source_url or "本地",
            ])
            node.setData(0, Qt.UserRole, g.id)
            if not exists:
                # 失效记录用灰色 + 提示，避免用户以为游戏还能打开
                from PySide6.QtGui import QColor, QBrush
                for col in range(5):
                    node.setForeground(col, QBrush(QColor("#6b7280")))
                node.setToolTip(0, f"文件不存在：{g.local_path}\n右键可移除该记录")
            tree.addTopLevelItem(node)

    def _refresh_library(self) -> None:
        kw = self.edit_search.text().strip() if hasattr(self, "edit_search") else ""
        self._fill_tree(self.tree_lib, list_games(keyword=kw))

    def _refresh_favorites(self) -> None:
        cats = ["全部"] + categories()
        cur = self.combo_fav_cat.currentText()
        self.combo_fav_cat.blockSignals(True)
        self.combo_fav_cat.clear()
        self.combo_fav_cat.addItems(cats)
        if cur in cats:
            self.combo_fav_cat.setCurrentText(cur)
        self.combo_fav_cat.blockSignals(False)
        cat = self.combo_fav_cat.currentText()
        games = list_games(favorite_only=True, category="" if cat in ("", "全部") else cat)
        self._fill_tree(self.tree_fav, games)

    def _selected_game(self, tree: QTreeWidget) -> Optional[Game]:
        item = tree.currentItem()
        if not item:
            return None
        gid = item.data(0, Qt.UserRole)
        return get_game(int(gid)) if gid is not None else None

    # -------------------------------------------------- 播放

    def _play_item(self, item, column) -> None:
        tree = self.sender()
        gid = item.data(0, Qt.UserRole)
        g = get_game(int(gid)) if gid is not None else None
        if g:
            self._play(g)

    def _play(self, g: Game) -> None:
        if not g.local_path or not os.path.exists(g.local_path):
            box = QMessageBox(self)
            box.setWindowTitle("找不到游戏")
            box.setText(f"《{g.name}》的文件已不在原位置。")
            box.setInformativeText(f"记录的路径：\n{g.local_path}\n\n"
                                   "可能已被移动或删除。你可以移除这条记录。")
            b_rm = box.addButton("移除记录", QMessageBox.DestructiveRole)
            box.addButton("知道了", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_rm:
                delete_game(g.id)
                self._refresh_library()
                self._refresh_favorites()
            return
        from ..core.db import touch_played
        p = GamePlayer(g)
        p.closed.connect(self._on_player_closed)
        self._players.append(p)
        p.show()
        touch_played(g.id)

    def _on_player_closed(self, gid: int) -> None:
        self._players = [p for p in self._players if p.isVisible()]

    # -------------------------------------------------- 菜单动作

    def _tree_menu(self, pos, tree: QTreeWidget) -> None:
        g = self._selected_game(tree)
        if not g:
            return
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu{{background:{SURFACE_2};color:{TEXT};border:1px solid #2a2f3a;}}
            QMenu::item:selected{{background:{ACCENT};}}
        """)
        menu.addAction("开始游戏", lambda: self._play(g))
        act_fav = menu.addAction("取消收藏" if g.favorite else "加入收藏")
        menu.addAction("重命名…", lambda: self._rename_game(tree))
        menu.addAction("移动分类…", lambda: self._move_category(tree))
        menu.addAction("打开文件夹", lambda: self._open_folder_of(g))
        menu.addSeparator()
        if not is_available(g):
            menu.addAction("移除失效记录", lambda: self._remove_one(g))
        menu.addAction("删除记录…", lambda: self._delete_selected(tree))
        chosen = menu.exec(tree.viewport().mapToGlobal(pos))
        if chosen is act_fav:
            update_game(g.id, favorite=0 if g.favorite else 1)
            self._refresh_library()
            self._refresh_favorites()

    def _remove_one(self, g: Game) -> None:
        delete_game(g.id)
        self._refresh_library()
        self._refresh_favorites()
        self.statusBar().showMessage(f"已移除记录：{g.name}")

    def _rename_game(self, tree=None) -> None:
        tree = tree or self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.tree_fav
        g = self._selected_game(tree)
        if not g:
            QMessageBox.information(self, "提示", "请先选中一个游戏")
            return
        name, ok = QInputDialog.getText(self, "重命名", "游戏名：", text=g.name)
        if ok and name.strip():
            update_game(g.id, name=name.strip())
            self._refresh_library()
            self._refresh_favorites()

    def _move_category(self, tree: QTreeWidget) -> None:
        """把选中游戏移动到指定分类。"""
        g = self._selected_game(tree)
        if not g:
            QMessageBox.information(self, "提示", "请先选中一个游戏")
            return
        cats = categories() or []
        for c in ("未分类", "动作", "益智", "射击", "体育", "休闲"):
            if c not in cats:
                cats.append(c)
        name, ok = QInputDialog.getItem(
            self, "移动分类", f"把《{g.name}》移动到：", cats,
            current=max(0, cats.index(g.category)) if g.category in cats else 0,
            editable=True)
        if ok and name.strip():
            update_game(g.id, category=name.strip())
            self._refresh_library()
            self._refresh_favorites()

    def _open_folder_of(self, g: Game) -> None:
        self._open_folder_of_impl(g)

    def _open_folder(self, tree: QTreeWidget) -> None:
        g = self._selected_game(tree)
        if g:
            self._open_folder_of_impl(g)

    def _open_folder_of_impl(self, g: Game) -> None:
        import subprocess
        path = g.local_path if os.path.isdir(g.local_path) else os.path.dirname(g.local_path)
        if not os.path.exists(path):
            QMessageBox.warning(self, "打不开", f"路径不存在：{path}")
            return
        try:
            os.startfile(path)          # Windows
        except Exception:
            try:
                subprocess.Popen(["explorer", path])
            except Exception as e:
                QMessageBox.warning(self, "打不开", str(e))

    def _delete_selected(self, tree: QTreeWidget) -> None:
        g = self._selected_game(tree)
        if not g:
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除")
        box.setText(f"删除《{g.name}》？")
        box.setInformativeText("仅删除软件内的记录。是否同时删除已下载的文件？")
        b_yes = box.addButton("连同文件删除", QMessageBox.DestructiveRole)
        b_no = box.addButton("只删记录", QMessageBox.RejectRole)
        b_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)
        box.exec()
        if box.clickedButton() is b_yes:
            delete_game(g.id, also_files=True)
        elif box.clickedButton() is b_no:
            delete_game(g.id, also_files=False)
        else:
            return
        self._refresh_library()
        self._refresh_favorites()

    def _clean_missing(self) -> None:
        """移除本地文件已不存在的记录。"""
        missing = find_missing()
        if not missing:
            QMessageBox.information(self, "无需清理", "所有游戏文件都在原位。")
            return
        names = "\n".join(f"· {g.name}" for g in missing[:15])
        more = f"\n… 等共 {len(missing)} 个" if len(missing) > 15 else ""
        box = QMessageBox(self)
        box.setWindowTitle("清理失效记录")
        box.setText(f"有 {len(missing)} 个游戏的文件已不在原位置：")
        box.setInformativeText(f"{names}{more}\n\n继续将只删除软件内的记录，"
                               "不会碰磁盘上的任何文件。")
        b_ok = box.addButton("移除这些记录", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not b_ok:
            return
        n = prune_missing()
        self._refresh_library()
        self._refresh_favorites()
        self.statusBar().showMessage(f"已清理 {n} 条失效记录")

    def _new_category(self) -> None:
        name, ok = QInputDialog.getText(self, "新建分类", "分类名：")
        if ok and name.strip():
            self.combo_fav_cat.addItem(name.strip())
            self.combo_fav_cat.setCurrentText(name.strip())

    def _rename_category(self) -> None:
        old = self.combo_fav_cat.currentText()
        if not old or old == "全部":
            return
        new, ok = QInputDialog.getText(self, "重命名分类", "新名称：", text=old)
        if ok and new.strip():
            rename_category(old, new.strip())
            self._refresh_favorites()

    def _unfavorite(self) -> None:
        g = self._selected_game(self.tree_fav)
        if not g:
            return
        update_game(g.id, favorite=0)
        self._refresh_library()
        self._refresh_favorites()

    # -------------------------------------------------- 设置

    def _save_settings(self) -> None:
        config.set("download_dir", self.set_dir.text().strip())
        config.set("proxy", self.set_proxy.text().strip())
        config.set("default_volume", self.set_volume.value())
        config.set("mcp_enabled", self.set_mcp.isChecked())
        port = int(self.set_port.value())
        config.set("mcp_port", port)
        config.set("lazy_fill", self.set_lazy.isChecked())
        config.set("max_depth", self.set_depth.value())
        config.save()
        self.edit_dir.setText(config.get("download_dir"))
        if self.set_mcp.isChecked():
            self._start_mcp(port)
        else:
            self._stop_mcp()
        QMessageBox.information(self, "已保存", "设置已保存。")

    def _start_mcp(self, port: int) -> None:
        try:
            if self._mcp_server:
                self._mcp_server.stop()
            from ..mcp.server import McpServer
            self._mcp_server = McpServer(host=config.get("mcp_host"), port=port)
            self._mcp_server.start()
            self.lbl_mcp.setText(f"MCP 已启动：http://{config.get('mcp_host')}:{port}")
        except Exception as e:
            self.lbl_mcp.setText(f"MCP 启动失败：{e}")

    def _stop_mcp(self) -> None:
        if self._mcp_server:
            try:
                self._mcp_server.stop()
            except Exception:
                pass
            self._mcp_server = None
        self.lbl_mcp.setText("MCP 已停止")

    def closeEvent(self, event):
        for w in self._workers:
            try:
                if w.isRunning():
                    w.quit(); w.wait(1500)
            except Exception:
                pass
        self._stop_mcp()
        super().closeEvent(event)
