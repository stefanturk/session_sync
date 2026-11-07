"""The few things that differ between a Mac and a Windows PC.

Everything else in Session Sync is the same code on both; keep the
differences here so they are easy to find.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

#: What the user calls the app that shows folders.
FILE_MANAGER = "File Explorer" if IS_WINDOWS else "Finder"
#: The save shortcut in Live.
SAVE_KEYS = "Ctrl+S" if IS_WINDOWS else "⌘S"


def strftime(when: datetime, pattern: str) -> str:
    """`strftime` that understands `%-I` / `%-d` (no leading zero) on Windows too."""
    if IS_WINDOWS:
        pattern = pattern.replace("%-", "%#")
    return when.strftime(pattern)


def quiet_flags() -> dict:
    """subprocess.run keywords that stop a console window flashing up on Windows."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def open_with_default_app(path: Path) -> None:
    """Open a file the way double-clicking it would (a .als opens in Live)."""
    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606 - the user's own file
    elif IS_MAC:
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def app_data_dir() -> Path:
    """Where the app keeps its log: ~/Library/Logs, or %LOCALAPPDATA%\\SessionSync."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SessionSync"
    return Path.home() / "Library" / "Logs"


def log_file() -> Path:
    return app_data_dir() / "SessionSync.log"


def log(text: str) -> None:
    """Append a line to the app's log; never raises."""
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass
