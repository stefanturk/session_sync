"""macOS notifications, so news arrives while you are inside Ableton.

The whole point of the presence layer is telling you something before you spend
two hours recording, and you will not be looking at this window while you do it.
"""

from __future__ import annotations

import subprocess
import sys

# Full paths: a double-clicked .app inherits a minimal PATH, so a bare command
# name is not guaranteed to resolve the way it does in a shell.
OSASCRIPT = "/usr/bin/osascript"


def notification_script(message: str, *, title: str) -> str:
    """One line of AppleScript. Project names contain apostrophes and quotes."""
    return (
        f'display notification "{_quote(message)}" '
        f'with title "{_quote(title)}"'
    )


def _quote(text: str) -> str:
    # Backslashes first, or the escapes added below would themselves be escaped.
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return " ".join(escaped.split("\n"))


def notify(message: str, *, title: str = "SessionSync") -> None:
    """Best effort — a notification that fails must never interrupt the app."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            [OSASCRIPT, "-e", notification_script(message, title=title)],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class Notifier:
    """Banners you can click.

    `osascript` banners cannot carry an action - macOS sends their click to
    Script Editor, which is how a banner ends up opening a text file. Qt's tray
    messages do report the click, so a banner about "Song A" can take you to
    Song A. Where that is unavailable it falls back to the plain banner.
    """

    def __init__(self, on_click=None):
        import os

        self.on_click = on_click
        self._target = None
        self._tray = None
        self.disabled = bool(os.environ.get("SESSIONSYNC_NO_NOTIFY"))

    def _ensure_tray(self):
        if self._tray is not None:
            return self._tray
        from PySide6.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        from sessionsync.ui import theme
        from sessionsync.ui.widgets import dot_icon

        tray = QSystemTrayIcon(dot_icon(theme.EDGE_LIT))
        tray.setToolTip("SessionSync")
        tray.messageClicked.connect(self._clicked)
        tray.show()
        self._tray = tray
        return tray

    def show(self, message: str, *, title: str = "SessionSync", target=None) -> None:
        if self.disabled:
            return
        self._target = target
        try:
            tray = self._ensure_tray()
        except Exception:  # noqa: BLE001 - a banner must never break the app
            tray = None
        if tray is None:
            notify(message, title=title)
            return
        from PySide6.QtWidgets import QSystemTrayIcon

        tray.showMessage(title, message, QSystemTrayIcon.Information, 10000)

    def _clicked(self) -> None:
        if self.on_click is not None:
            self.on_click(self._target)
