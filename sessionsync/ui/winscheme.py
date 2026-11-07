"""Windows: make "▶ Open in Live (Windows)" links open Session Sync.

The installer registers the `sessionsync:` link type; the app registers it
again each time it starts, so moving the app folder (or running it unzipped,
without the installer) still works. Per-user (HKEY_CURRENT_USER), so no
administrator prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sessionsync.core.launcher import SCHEME

CLASSES = r"Software\Classes"


def command_for(exe: Path) -> str:
    return f'"{exe}" "%1"'


def register(exe: Path, *, scheme: str = SCHEME, classes: str = CLASSES) -> bool:
    """Point `scheme:` links at `exe`. Returns True if anything was written."""
    import winreg

    base = rf"{classes}\{scheme}"
    wanted = {
        (base, ""): "URL:Session Sync",
        (base, "URL Protocol"): "",
        (rf"{base}\DefaultIcon", ""): f'"{exe}",0',
        (rf"{base}\shell\open\command", ""): command_for(exe),
    }
    if all(_read(winreg, key, value) == data for (key, value), data in wanted.items()):
        return False
    for (key, value), data in wanted.items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, value, 0, winreg.REG_SZ, data)
    return True


def registered_command(*, scheme: str = SCHEME, classes: str = CLASSES) -> str | None:
    import winreg

    return _read(winreg, rf"{classes}\{scheme}\shell\open\command", "")


def unregister(*, scheme: str = SCHEME, classes: str = CLASSES) -> None:
    import winreg

    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{scheme}{sub}")
        except OSError:
            pass


def register_this_app() -> None:
    """At startup, for the built app only (a dev run must not take over the links)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        register(Path(sys.executable).resolve())
    except OSError:
        pass  # a locked-down PC: the installer's registration still stands


def _read(winreg, key: str, value: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            data, _ = winreg.QueryValueEx(handle, value)
            return data
    except OSError:
        return None
