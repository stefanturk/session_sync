"""Starting the app: who you are, and which project to open."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog

from sessionsync.auth.identity import IdentityNotSetError, load_identity, save_identity
from sessionsync.ui.settings import Settings
from sessionsync.ui.window import MainWindow


def project_argument(argv: list[str]) -> Path | None:
    """The project folder asked for on the command line, if any.

    A .app launched from Finder is handed a `-psn_0_12345` process serial
    number, which must not be mistaken for a path.
    """
    for arg in argv:
        if not arg.startswith("-"):
            return Path(arg).expanduser()
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    app = QApplication([sys.argv[0] if sys.argv else "sessionsync"])
    app.setApplicationName("SessionSync")
    app.setApplicationDisplayName("SessionSync")
    # The window is dark whatever the system is set to, so ask for the dark
    # appearance — otherwise macOS draws a light title bar around it.
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:  # older Qt; the window is still dark, the frame is not
        pass

    if not _ensure_identity():
        return 1

    window = MainWindow(Settings())
    window.show()
    window.start_hub_watch()

    # The start screen is the front door; a path on the command line skips it.
    project = project_argument(argv)
    if project is not None:
        window.open_project(project)

    return app.exec()


def _ensure_identity() -> bool:
    """Versions carry a name; ask for one once rather than failing later."""
    try:
        load_identity()
        return True
    except IdentityNotSetError:
        pass

    name, ok = QInputDialog.getText(None, "Welcome to SessionSync", "Your name:")
    if not ok or not name.strip():
        return False
    email, ok = QInputDialog.getText(None, "Welcome to SessionSync", "Your email:")
    if not ok:
        return False
    save_identity(name.strip(), email.strip())
    return True


if __name__ == "__main__":
    raise SystemExit(main())
