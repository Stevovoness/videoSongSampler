"""Dark theme."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#ffb627"
OK = "#4cc38a"
WARN = "#f5a524"
BAD = "#f05252"
OCTAVE = "#5aa9ff"   # note played from a clip an octave away
BG = "#17181c"
PANEL = "#202228"
PANEL2 = "#2a2d35"
TEXT = "#e8e8ec"
MUTED = "#9a9caa"

QSS = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{
    background: {PANEL}; color: {MUTED}; padding: 10px 22px; margin-right: 4px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; font-size: 11pt; font-weight: 600;
}}
QTabBar::tab:selected {{ background: {PANEL2}; color: {TEXT}; border-bottom: 3px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QGroupBox {{
    background: {PANEL}; border: 1px solid #30333c; border-radius: 10px;
    margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT}; }}
QPushButton {{
    background: {PANEL2}; color: {TEXT}; border: 1px solid #3a3e48; border-radius: 7px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #343844; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #2a2d35; }}
QPushButton:disabled {{ color: #666; border-color: #2c2f36; }}
QPushButton[primary="true"] {{
    background: {ACCENT}; color: #1a1300; border: none; font-weight: 700; padding: 10px 22px; font-size: 11pt;
}}
QPushButton[primary="true"]:hover {{ background: #ffc54f; }}
QPushButton[primary="true"]:disabled {{ background: #6b5a33; color: #2a2414; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #121317; color: {TEXT}; border: 1px solid #3a3e48; border-radius: 6px; padding: 4px 6px;
}}
QListWidget {{ background: #121317; border: 1px solid #30333c; border-radius: 8px; color: {TEXT}; }}
QListWidget::item {{ padding: 4px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #3a3420; color: {TEXT}; }}
QProgressBar {{
    background: #121317; border: 1px solid #30333c; border-radius: 7px; text-align: center; color: {TEXT}; height: 22px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QLabel[hint="true"] {{ color: {MUTED}; }}
QLabel[h1="true"] {{ font-size: 15pt; font-weight: 700; color: {TEXT}; }}
QFrame[card="true"] {{ background: {PANEL}; border: 1px solid #30333c; border-radius: 10px; }}
QFrame[warn="true"] {{ background: #3a2a10; border: 1px solid {WARN}; border-radius: 10px; }}
QFrame[good="true"] {{ background: #12301f; border: 1px solid {OK}; border-radius: 10px; }}
QScrollArea {{ border: none; background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; background: #121317; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {ACCENT}; padding: 4px; }}
QStatusBar {{ color: {MUTED}; }}
QMenuBar {{ background: {BG}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: {PANEL2}; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid #30333c; }}
QMenu::item:selected {{ background: #3a3420; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    for role, c in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, "#121317"),
        (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT), (QPalette.Button, PANEL2),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#1a1300"),
        (QPalette.ToolTipBase, PANEL2), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED),
    ]:
        p.setColor(role, QColor(c))
    app.setPalette(p)
    app.setStyleSheet(QSS)
