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
