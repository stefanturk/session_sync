"""Starting the app: who you are, and which project to open."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from sessionsync.auth.identity import IdentityNotSetError, load_identity, save_identity
from sessionsync.core.localstate import LocalState
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

    if not _ensure_identity():
        return 1

    settings = Settings()
    project = project_argument(argv) or settings.last_project()
    if project is None or not LocalState.exists(project):
        project = _choose_project()
    if project is None:
        return 0

    settings.remember(project)
    window = MainWindow(project, settings)
    window.show()
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


def _choose_project() -> Path | None:
    while True:
        folder = QFileDialog.getExistingDirectory(None, "Choose a SessionSync project folder")
        if not folder:
            return None
        project = Path(folder)
        if LocalState.exists(project):
            return project

        QMessageBox.warning(
            None,
            "Not set up yet",
            f"'{project.name}' is not synced with SessionSync yet.\n\n"
            "Set it up from the command line first:\n"
            f"    sessionsync init '{project}' --remote <where the repository lives>",
        )


if __name__ == "__main__":
    raise SystemExit(main())
