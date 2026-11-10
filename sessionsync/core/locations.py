"""Where your own copy of a song lives, and the places it must never live.

Your copy has to be on this computer only. Inside a synced folder (Google
Drive, Dropbox, OneDrive, iCloud Drive) it would upload gigabytes nobody asked
for, and — worse — the sync app may remove files "to save space", which Live
then reports as missing samples in the middle of a session.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: Room to spare after a pull, so Live can still record and save.
SPARE_BYTES = 2 * 1024**3

_SYNCED_NAMES = (
    "My Drive", "Google Drive", "GoogleDrive", "Dropbox", "OneDrive", "Shared drives",
    "iCloudDrive", "iCloud Drive",
)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""      # why it is refused
    warning: str = ""     # allowed, but worth saying


def home() -> Path:
    return Path(os.environ.get("SESSIONSYNC_USER_HOME") or Path.home())


def desktop() -> Path:
    """The Desktop. On Windows it may have been moved (often into OneDrive)."""
    if os.environ.get("SESSIONSYNC_USER_HOME") is None and sys.platform == "win32":
        moved = _windows_desktop()
        if moved is not None:
            return moved
    return home() / "Desktop"


def _windows_desktop() -> Path | None:
    """Explorer's own record of where the Desktop is (User Shell Folders)."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
    except (ImportError, OSError):
        return None
    path = Path(os.path.expandvars(value))
    return path if path.is_dir() else None


def fallback_root() -> Path:
    return home() / "Music" / "Session Sync"


def desktop_in_icloud() -> bool:
    """macOS "Desktop & Documents Folders" in iCloud: the Desktop is a synced folder."""
    return (home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Desktop").is_dir()


def default_root() -> Path:
    """The Desktop, unless iCloud (or OneDrive) would sync it — then Music/Session Sync."""
    if not (desktop_in_icloud() or is_synced(desktop())):
        return desktop()
    if not is_synced(fallback_root()):
        return fallback_root()
    return home() / "Session Sync"


def is_synced(path: Path) -> bool:
    path = Path(path).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    parts = path.parts
    text = path.as_posix()
    if "/Library/CloudStorage/" in text or "/Library/Mobile Documents/" in text:
        return True
    return any(part == "Box" or part.startswith(_SYNCED_NAMES) for part in parts)


def is_cloud_root(path: Path) -> bool:
    """Is `path` the very top of a synced drive (My Drive, Dropbox, OneDrive, iCloud Drive...)?

    Nothing of ours may live directly there: it is where everything else in the
    drive is, and Drive can "restore" stray files there long after we move them.
    """
    path = Path(path).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    text = path.as_posix()
    if text.endswith("/Library/Mobile Documents/com~apple~CloudDocs"):
        return True
    if path.parent.name == "CloudStorage" and path.parent.parent.name == "Library":
        return True  # ~/Library/CloudStorage/GoogleDrive-… (the account folder)
    if path.anchor and path == Path(path.anchor) and sys.platform == "win32":
        return is_synced(path) or (path / "My Drive").is_dir()  # G:\ for Drive for desktop
    name = path.name
    if name.startswith(("My Drive", "Shared drives")):
        return True
    if name.startswith(_SYNCED_NAMES) or name == "Box":
        return not is_synced(path.parent)
    return False


def check_location(folder: Path, *, hubs: list[Path] = (), songs: list[Path] = ()) -> Verdict:
    """May new songs be put in `folder`?"""
    folder = Path(folder).expanduser()
    if is_synced(folder):
        return Verdict(False, "That folder is synced to the cloud. Your copy of a song must live "
                              "only on this computer — choose a folder outside Google Drive, "
                              "Dropbox, OneDrive and iCloud Drive.")
    resolved = _resolved(folder)
    for hub in hubs:
        if _inside(resolved, _resolved(hub)):
            return Verdict(False, "That is inside a shared folder. Choose a folder on this computer.")
    for song in songs:
        if _inside(resolved, _resolved(song)):
            return Verdict(False, f"That is inside your copy of {Path(song).name}. Choose another folder.")
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        return Verdict(False, "Session Sync cannot write there. Choose a folder you can save into.")
    if _resolved(folder) == _resolved(desktop()) and desktop_in_icloud():
        return Verdict(True, warning="Your Desktop is stored in iCloud, which can remove song files "
                                     "to save space. Music/Session Sync is safer.")
    return Verdict(True)


def free_folder(parent: Path, name: str) -> Path:
    """`parent/name`, or `name (Session Sync)`, `… 2` — never a folder that already exists."""
    parent = Path(parent)
    candidate = parent / name
    if not candidate.exists():
        return candidate
    candidate = parent / f"{name} (Session Sync)"
    n = 2
    while candidate.exists():
        candidate = parent / f"{name} (Session Sync) {n}"
        n += 1
    return candidate


def room_for(folder: Path, needed: int) -> tuple[bool, int]:
    """(enough space?, bytes free) on the disk `folder` would be on."""
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    return free >= needed + SPARE_BYTES, free


def _resolved(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser()


def _inside(path: Path, other: Path) -> bool:
    return path == other or other in path.parents
