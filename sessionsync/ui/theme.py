"""The window's look, in one place.

Borrowed from Beat Thief, which sits open next to Ableton all day and so is
built as a piece of hardware rather than a web page: a dark panel, silkscreen
section labels, and one lit call-to-action. Set in Avenir Next, which ships
with macOS, so nothing is ever fetched and nothing falls back to Helvetica.

The one addition here is amber. Beat Thief only ever needed good/bad; being
*behind* is neither — it is not an error, it is a "stop and pull first", and it
has to be distinguishable at a glance from having unpushed work.
"""

from __future__ import annotations

PANEL = "#16171b"
RAISED = "#1e2026"
SUNKEN = "#0d0e11"
EDGE = "#2c2f37"
EDGE_LIT = "#3d414b"
INK = "#e9e7e2"
DIM = "#8b8d95"
FAINT = "#5b5d64"

VIOLET = "#a78bfa"
VIOLET_DIM = "#38265f"
GOOD = "#5fd39a"
WARN = "#f0b542"
BAD = "#ff6b5e"

RADIUS = 6

UI_FONT = '"Avenir Next", "Avenir", -apple-system, sans-serif'

#: banner level -> (accent, translucent fill)
LEVELS = {
    "ok": (GOOD, "rgba(95, 211, 154, 0.10)"),
    "info": (VIOLET, "rgba(167, 139, 250, 0.12)"),
    "warning": (WARN, "rgba(240, 181, 66, 0.13)"),
    "danger": (BAD, "rgba(255, 107, 94, 0.13)"),
}


def level_colours(level: str) -> tuple[str, str]:
    return LEVELS.get(level, LEVELS["ok"])


STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {PANEL};
}}

QWidget {{
    color: {INK};
    font-family: {UI_FONT};
    font-size: 13px;
}}

QToolTip {{
    background: {RAISED};
    color: {INK};
    border: 1px solid {EDGE};
    padding: 6px 8px;
}}

/* Silkscreen labels, the way a panel names its sections. */
QLabel#silkscreen {{
    color: {DIM};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
}}

QLabel#projectName {{
    font-size: 24px;
    font-weight: 600;
}}

QLabel#versions {{
    color: {DIM};
    font-size: 12px;
}}

QLabel#stateLabel {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
    color: {DIM};
}}

QLabel#presence {{
    color: {INK};
    font-size: 12px;
}}

QLabel#summary, QLabel#lastPush {{
    color: {FAINT};
    font-size: 11px;
}}

QFrame#header {{
    background: {PANEL};
    border-bottom: 1px solid {EDGE};
}}

QFrame#rule {{
    background: {EDGE};
    max-height: 1px;
    border: none;
}}

/* Pads: the actions you can take. Off by default, lit when recommended. */
QPushButton {{
    background: {RAISED};
    border: 1px solid {EDGE};
    border-radius: {RADIUS}px;
    color: {DIM};
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 1.6px;
    padding: 13px 8px 11px;
}}
QPushButton:hover:enabled {{
    border-color: {EDGE_LIT};
    color: {INK};
}}
QPushButton:pressed:enabled {{
    background: {SUNKEN};
}}
QPushButton:disabled {{
    color: #4a4d55;
    border-color: #24262c;
    background: #191b20;
}}

QPushButton[primary="true"] {{
    background: {VIOLET};
    border: 1px solid {VIOLET};
    color: #14102a;
    font-weight: 700;
}}
QPushButton[primary="true"]:hover:enabled {{
    background: #b9a1fb;
    border-color: #b9a1fb;
    color: #14102a;
}}
QPushButton[primary="true"]:pressed:enabled {{
    background: #9179e8;
}}

/* The change list. Sunken, like a readout. */
QTreeWidget {{
    background: {SUNKEN};
    border: 1px solid {EDGE};
    border-radius: {RADIUS}px;
    font-size: 12px;
    outline: none;
    padding: 4px;
}}
/* No colour here: a stylesheet rule beats the per-row brush, and the change
   kind is colour-coded (added green, modified violet, removed red). */
QTreeWidget::item {{
    padding: 5px 4px;
}}
/* The header is its own widget with its own palette; "transparent" leaves it
   drawing the system light grey rather than the panel underneath. */
QHeaderView {{
    background: {SUNKEN};
    border: none;
}}
QHeaderView::section {{
    background: {SUNKEN};
    border: none;
    border-bottom: 1px solid {EDGE};
    color: {DIM};
    font-size: 9.5px;
    font-weight: 600;
    letter-spacing: 1.4px;
    padding: 7px 4px;
}}
QTreeWidget QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 0;
}}
QTreeWidget QScrollBar::handle:vertical {{
    background: {EDGE_LIT};
    border-radius: 4px;
    min-height: 24px;
}}
QTreeWidget QScrollBar::add-line, QTreeWidget QScrollBar::sub-line {{
    height: 0;
}}

QTextEdit {{
    background: {SUNKEN};
    border: 1px solid {EDGE};
    border-radius: {RADIUS}px;
    color: {INK};
    padding: 10px;
}}

QStatusBar {{
    background: {PANEL};
    border-top: 1px solid {EDGE};
    color: {DIM};
    font-size: 11px;
}}
QStatusBar::item {{ border: none; }}

QMenuBar {{ background: {PANEL}; color: {INK}; }}
QMenuBar::item:selected {{ background: {RAISED}; }}
QMenu {{ background: {RAISED}; border: 1px solid {EDGE}; }}
QMenu::item:selected {{ background: {VIOLET_DIM}; }}

QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {INK}; }}
QInputDialog {{ background: {PANEL}; }}
QLineEdit {{
    background: {SUNKEN};
    border: 1px solid {EDGE};
    border-radius: {RADIUS}px;
    color: {INK};
    padding: 9px 11px;
    selection-background-color: {VIOLET_DIM};
}}
QLineEdit:focus {{ border-color: {VIOLET}; }}
"""
