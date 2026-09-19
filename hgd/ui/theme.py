"""统一配色与控件样式。

深色主题：游戏画面是主体，UI 应该退到背景里。
"""
from __future__ import annotations

BG = "#0f1115"
SURFACE = "#171a21"
SURFACE_2 = "#1e222b"
BORDER = "#2a2f3a"
TEXT = "#e6e9ef"
TEXT_MUTED = "#9aa3b2"
ACCENT = "#5b9cff"
ACCENT_HOVER = "#7ab0ff"
DANGER = "#e5534b"
SUCCESS = "#3fb950"


def speaker_icon(muted: bool, size: int = 18) -> "QIcon":
    """用 QPainter 画喇叭图标。

    不用 emoji：Windows 的 Fusion 样式下彩色 emoji 常渲染成方块/黑白，
    矢量绘制在任何环境都稳定，也能按状态改色。
    """
    from PySide6.QtCore import QRectF, Qt, QPointF
    from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap, QColor, QPen

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    color = QColor(DANGER if muted else TEXT)

    s = size
    # 喇叭主体：一个梯形 + 矩形
    body = QPainterPath()
    body.moveTo(s * 0.10, s * 0.36)
    body.lineTo(s * 0.26, s * 0.36)
    body.lineTo(s * 0.46, s * 0.18)
    body.lineTo(s * 0.46, s * 0.82)
    body.lineTo(s * 0.26, s * 0.64)
    body.lineTo(s * 0.10, s * 0.64)
    body.closeSubpath()
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawPath(body)

    if muted:
        # 静音：画一个叉
        pen = QPen(color, max(1.6, s * 0.10))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.60, s * 0.36), QPointF(s * 0.84, s * 0.64))
        p.drawLine(QPointF(s * 0.84, s * 0.36), QPointF(s * 0.60, s * 0.64))
    else:
        # 有声：画两道声波弧
        pen = QPen(color, max(1.4, s * 0.085))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r1 = QRectF(s * 0.50, s * 0.34, s * 0.22, s * 0.32)
        p.drawArc(r1, -60 * 16, 120 * 16)
        r2 = QRectF(s * 0.50, s * 0.22, s * 0.40, s * 0.56)
        p.drawArc(r2, -55 * 16, 110 * 16)

    p.end()
    return QIcon(pm)


def button_style(primary: bool = True) -> str:
    if primary:
        return f"""
        QPushButton {{
            background:{ACCENT}; color:#fff; border:0; border-radius:6px;
            padding:7px 16px; font-size:13px; font-weight:600;
        }}
        QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        QPushButton:pressed {{ background:#4a89e8; }}
        QPushButton:disabled {{ background:#3a4250; color:#7a8496; }}
        """
    return f"""
    QPushButton {{
        background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER};
        border-radius:6px; padding:6px 12px; font-size:12px;
    }}
    QPushButton:hover {{ background:#262b36; border-color:{ACCENT}; }}
    QPushButton:pressed {{ background:#20242e; }}
    """


def toolbar_style() -> str:
    return f"""
    QFrame#toolbar {{
        background:{SURFACE}; border-bottom:1px solid {BORDER};
    }}
    QFrame#toolbar QPushButton {{ }}
    """


def input_style() -> str:
    return f"""
    QLineEdit {{
        background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER};
        border-radius:6px; padding:8px 10px; font-size:13px;
    }}
    QLineEdit:focus {{ border-color:{ACCENT}; }}
    """


def combo_style() -> str:
    return f"""
    QComboBox {{
        background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER};
        border-radius:6px; padding:6px 10px; font-size:12px; min-width:100px;
    }}
    QComboBox:hover {{ border-color:{ACCENT}; }}
    QComboBox QAbstractItemView {{
        background:{SURFACE_2}; color:{TEXT}; selection-background-color:{ACCENT};
        border:1px solid {BORDER};
    }}
    """


def list_style() -> str:
    return f"""
    QListWidget, QTreeWidget {{
        background:{BG}; color:{TEXT}; border:1px solid {BORDER}; border-radius:8px;
        outline:0; font-size:13px;
    }}
    QListWidget::item, QTreeWidget::item {{ padding:8px 10px; border-radius:4px; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background:{SURFACE_2}; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background:{ACCENT}; color:#fff;
    }}
    """


def app_style() -> str:
    return f"""
    QMainWindow, QWidget {{ background:{BG}; color:{TEXT};
        font-family:"Microsoft YaHei","Segoe UI",sans-serif; font-size:13px; }}
    QLabel {{ color:{TEXT}; min-height:18px; }}
    QCheckBox {{ color:{TEXT}; min-height:20px; spacing:6px; }}
    QSpinBox, QLineEdit {{ min-height:20px; }}
    QTabWidget::pane {{ border:1px solid {BORDER}; border-top:0; background:{BG}; }}
    QTabBar::tab {{
        background:{SURFACE}; color:{TEXT_MUTED}; padding:8px 20px;
        border:1px solid {BORDER}; border-bottom:0; margin-right:2px;
    }}
    QTabBar::tab:selected {{ background:{BG}; color:{ACCENT}; font-weight:600; }}
    QTabBar::tab:hover {{ color:{TEXT}; }}
    QGroupBox {{
        border:1px solid {BORDER}; border-radius:8px; margin-top:14px;
        padding-top:10px; color:{TEXT_MUTED};
    }}
    QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; }}
    QProgressBar {{
        background:{SURFACE_2}; border:0; border-radius:4px; height:8px; text-align:center;
    }}
    QProgressBar::chunk {{ background:{ACCENT}; border-radius:4px; }}
    QStatusBar {{ background:{SURFACE}; color:{TEXT_MUTED}; }}
    QMenuBar {{ background:{SURFACE}; color:{TEXT}; }}
    QMenuBar::item:selected {{ background:{SURFACE_2}; }}
    QMenu {{ background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER}; }}
    QMenu::item:selected {{ background:{ACCENT}; }}
    QToolTip {{ background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER}; }}
    QSplitter::handle {{ background:{BORDER}; }}
    """
