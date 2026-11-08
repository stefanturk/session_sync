"""Starting the app: who you are, and which project to open."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog

from sessionsync.auth.identity import IdentityNotSetError, load_identity, save_identity
from sessionsync.core import osinfo
from sessionsync.ui import single, winscheme
from sessionsync.ui.settings import Settings
from sessionsync.ui.window import MainWindow

#: SESSIONSYNC_SMOKE=<seconds>: start, log what arrives, quit — for build checks.
SMOKE = "SESSIONSYNC_SMOKE"


def project_argument(argv: list[str]) -> Path | None:
    """The project folder asked for on the command line, if any.

    A .app launched from Finder is handed a `-psn_0_12345` process serial
    number, which must not be mistaken for a path.
    """
    for arg in argv:
        if not arg.startswith("-") and not arg.startswith("sessionsync:"):
            return Path(arg).expanduser()
    return None


class SessionSyncApp(QApplication):
    """Catches the "▶ Open in Live" links in shared folders (sessionsync://…).

    macOS delivers a link as a FileOpen event — at launch, before the window
    exists, if that click is what started the app — so links are held until
    the window is ready to take them.
    """

    def __init__(self, args):
        super().__init__(args)
        self.window = None
        self.pending: list[str] = []

    def event(self, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.FileOpen:
            url = event.url().toString() if not event.url().isEmpty() else event.file()
            if url.startswith("sessionsync:"):
                self.deliver(url)
                return True
        return super().event(event)

    def deliver(self, url: str) -> None:
        osinfo.log(f"link: {url}")
        if os.environ.get(SMOKE):
            return  # a build check: never stop on a dialog nobody can see
        if self.window is None:
            self.pending.append(url)
        else:
            self.window.open_link(url)

    def window_ready(self, window) -> None:
        self.window = window
        for url in self.pending:
            window.open_link(url)
        self.pending.clear()


def _windows_icon(app) -> None:
    """On Windows, run from source with pythonw: Session Sync's own icon and
    taskbar group instead of Python's. (A Mac .app has its icon already.)"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Steezy.session_sync")
    except Exception:
        pass
    ico = Path(__file__).resolve().parents[2] / "icon.ico"
    if ico.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(ico)))


def link_argument(argv: list[str]) -> str | None:
    return next((a for a in argv if a.startswith("sessionsync:")), None)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    app = SessionSyncApp([sys.argv[0] if sys.argv else "sessionsync"])
    _windows_icon(app)

    # Already running? Hand over what this copy was started for, and stop.
    # (Windows starts a new copy for every "▶ Open in Live" click.)
    link = link_argument(argv)
    project = project_argument(argv)
    message = link or (str(project.resolve()) if project else single.SHOW)
    if single.forward(message):
        osinfo.log(f"handed to the running copy: {message}")
        return 0
    listener = single.Listener()
    if not listener.listen():
        osinfo.log("could not listen for other copies; carrying on alone")
    winscheme.register_this_app()
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
    window.start_live_watch()
    listener.received.connect(lambda text: _from_another_copy(app, window, text))

    if link:  # Windows passes the link on the command line
        app.deliver(link)
    app.window_ready(window)

    # The start screen is the front door; a path on the command line skips it.
    if project is not None:
        window.open_project(project)

    smoke = os.environ.get(SMOKE)
    if smoke:
        osinfo.log("smoke: window up")
        QTimer.singleShot(int(float(smoke) * 1000), app.quit)

    code = app.exec()
    listener.close()
    return code


def _from_another_copy(app: SessionSyncApp, window: MainWindow, text: str) -> None:
    osinfo.log(f"from another copy: {text}")
    window.show()
    window.raise_()
    window.activateWindow()
    if text == single.SHOW:
        return
    if text.startswith("sessionsync:"):
        app.deliver(text)
    elif not os.environ.get(SMOKE):
        window.open_project(Path(text))


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
