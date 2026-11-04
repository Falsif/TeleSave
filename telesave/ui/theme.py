"""Тёмная и светлая темы оформления."""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette

PALETTES = {
    "dark": dict(bg="#0f1720", panel="#17212b", panel2="#1e2a36", hover="#22303d", border="#263443",
                 text="#e7edf3", muted="#8b9bab", accent="#4ea4f6", accent_hover="#68b3fa", accent_soft="#1d3a57",
                 bubble_in="#1c2733", bubble_out="#2b5278", chat_bg="#0e1621", danger="#e5534b", fav="#f5c04a",
                 scroll="#2f3f50", on_accent="#ffffff", link="#7cc0ff", success="#4cc38a", warn="#e8a33d"),
    "light": dict(bg="#f3f5f8", panel="#ffffff", panel2="#f0f3f6", hover="#e8eef4", border="#dce3ea",
                  text="#17212b", muted="#6b7b8b", accent="#2f86e0", accent_hover="#3f95ee", accent_soft="#dcebfb",
                  bubble_in="#ffffff", bubble_out="#e2f4d3", chat_bg="#e6ecf1", danger="#d93f37", fav="#e0a514",
                  scroll="#c3cdd6", on_accent="#ffffff", link="#1f6fc5", success="#2e9e6a", warn="#c98410"),
}

C: dict = dict(PALETTES["dark"])
CURRENT = "dark"

QSS = Template(r"""
* { font-family: "Segoe UI", "Segoe UI Emoji", "Noto Sans", sans-serif; font-size: 10pt; color: $text; }
QMainWindow, QDialog, #Page { background: $bg; }
QWidget#Page { background: $bg; }
#Sidebar { background: $panel; border-right: 1px solid $border; }
#Logo { font-size: 15pt; font-weight: 700; }
#NavButton { text-align: left; padding: 9px 14px; border: none; border-radius: 10px; background: transparent; color: $muted; font-size: 10.5pt; }
#NavButton:hover { background: $hover; color: $text; }
#NavButton:checked { background: $accent_soft; color: $accent; font-weight: 600; }
#Account { color: $muted; font-size: 9pt; }
#H1 { font-size: 17pt; font-weight: 700; }
#H2 { font-size: 12pt; font-weight: 600; }
#Muted { color: $muted; }
#Small { color: $muted; font-size: 8.5pt; }
#Error { color: $danger; }
#Success { color: $success; }
#Card { background: $panel; border: 1px solid $border; border-radius: 14px; }
#Panel { background: $panel; border-right: 1px solid $border; }
#ChatHeader { background: $panel; border-bottom: 1px solid $border; }
QLineEdit, QDateEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {
    background: $panel2; border: 1px solid $border; border-radius: 8px; padding: 6px 8px;
    selection-background-color: $accent; selection-color: $on_accent; }
QLineEdit:focus, QDateEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QDateEdit:disabled { color: $muted; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $border; selection-background-color: $accent_soft; selection-color: $text; outline: 0; }
QPushButton { background: $panel2; border: 1px solid $border; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { border-color: $accent; background: $hover; }
QPushButton:pressed { background: $accent_soft; }
QPushButton:disabled { color: $muted; border-color: $border; }
QPushButton#Primary { background: $accent; color: $on_accent; border: none; font-weight: 600; }
QPushButton#Primary:hover { background: $accent_hover; }
QPushButton#Primary:disabled { background: $scroll; }
QPushButton#Danger { color: $danger; }
QPushButton#Danger:hover { border-color: $danger; }
QPushButton#Link { background: transparent; border: none; padding: 0; color: $muted; font-size: 8.5pt; text-decoration: underline; }
QPushButton#Link:hover { color: $accent; }
QToolButton { background: transparent; border: none; border-radius: 8px; padding: 4px; }
QToolButton:hover { background: $hover; }
QToolButton::menu-indicator { image: none; }
QListWidget { background: $panel; border: none; outline: 0; }
QListWidget::item { border-radius: 10px; margin: 1px 6px; }
QListWidget::item:selected { background: $accent_soft; }
QListWidget::item:hover:!selected { background: $hover; }
QListWidget#MediaGrid { background: $bg; }
QListWidget#MediaGrid::item { padding: 6px; }
QScrollArea { border: none; background: transparent; }
#MsgArea { background: $chat_bg; }
#SettingsBody { background: $bg; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QStatusBar { background: $panel; color: $muted; border-top: 1px solid $border; }
QStatusBar QLabel { color: $muted; font-size: 9pt; padding: 0 6px; }
QFrame#BubbleIn { background: $bubble_in; border-radius: 14px; border: 1px solid $bubble_in; }
QFrame#BubbleOut { background: $bubble_out; border-radius: 14px; border: 1px solid $bubble_out; }
QFrame#BubbleIn[deleted="true"], QFrame#BubbleOut[deleted="true"] { border: 1px solid $danger; }
QFrame#BubbleIn[focus="true"], QFrame#BubbleOut[focus="true"] { border: 2px solid $fav; }
#Sender { color: $accent; font-weight: 600; font-size: 9.5pt; }
#ChatRef { color: $accent; font-size: 8.5pt; }
#Fwd { color: $muted; font-style: italic; font-size: 9pt; }
#Meta { color: $muted; font-size: 8pt; }
#MediaNote { color: $muted; font-size: 9pt; }
#DeletedBadge { color: #ffffff; background: $danger; border-radius: 7px; padding: 1px 7px; font-size: 8pt; font-weight: 600; }
#DaySep { color: $muted; background: $panel; border-radius: 10px; padding: 3px 12px; font-size: 8.5pt; }
#Dot[state="online"] { color: $success; } #Dot[state="offline"] { color: $danger; }
#Dot[state="syncing"], #Dot[state="connecting"] { color: $warn; } #Dot[state="logged_out"] { color: $muted; }
QMenu { background: $panel; border: 1px solid $border; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 7px 22px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: $accent_soft; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QToolTip { background: $panel2; color: $text; border: 1px solid $border; padding: 4px; }
QCheckBox { spacing: 8px; }
QRadioButton { spacing: 8px; }
QSplitter::handle { background: $border; width: 1px; }
QProgressBar { background: $panel2; border: none; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QCalendarWidget QWidget { alternate-background-color: $panel2; }
""")


def apply(app, name: str) -> None:
    global CURRENT
    CURRENT = name if name in PALETTES else "dark"
    C.clear()
    C.update(PALETTES[CURRENT])
    pal = QPalette()
    roles = {QPalette.Window: "bg", QPalette.WindowText: "text", QPalette.Base: "panel2",
             QPalette.AlternateBase: "panel", QPalette.Text: "text", QPalette.Button: "panel2",
             QPalette.ButtonText: "text", QPalette.Highlight: "accent", QPalette.HighlightedText: "on_accent",
             QPalette.ToolTipBase: "panel2", QPalette.ToolTipText: "text", QPalette.Link: "link",
             QPalette.PlaceholderText: "muted"}
    for role, key in roles.items():
        pal.setColor(role, QColor(C[key]))
    app.setPalette(pal)
    app.setStyleSheet(QSS.substitute(C))
