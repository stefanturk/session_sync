"""Google Drive for desktop, as seen from this computer: is it here, and what is
a folder's web link (for the invite message).

Only reads. Drive's own metadata database is opened read-only; nothing is sent
anywhere. Every function returns None/False when it cannot tell.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

FOLDER_URL = "https://drive.google.com/drive/folders/{id}"
DOWNLOAD_URL = "https://www.google.com/drive/download/"

INSTALL_MAC = (
    'bash -c "$(curl -fsSL https://raw.githubusercontent.com/stefanturk/steezy_studio/main/install.sh)" '
    "_ session_sync"
)
INSTALL_WINDOWS = (
    "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/stefanturk/steezy_studio/main/install.ps1)))"
    " session_sync"
)


def drivefs_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Google" / "DriveFS"
    return Path.home() / "Library" / "Application Support" / "Google" / "DriveFS"


def drive_installed() -> bool:
    if sys.platform == "darwin":
        return Path("/Applications/Google Drive.app").exists() or drivefs_dir().is_dir()
    if sys.platform == "win32":
        program = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        return (program / "Google" / "Drive File Stream").is_dir() or drivefs_dir().is_dir()
    return False


def drive_roots() -> list[Path]:
    """Where Drive shows its files on this computer (My Drive, shortcuts' targets)."""
    found: list[Path] = []
    home = Path.home()
    if sys.platform == "darwin":
        cloud = home / "Library" / "CloudStorage"
        found += sorted(cloud.glob("GoogleDrive-*")) if cloud.is_dir() else []
        found += sorted(p for p in home.glob("My Drive*") if p.is_dir())
    elif sys.platform == "win32":
        for letter in "GHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            if (root / "My Drive").is_dir() or (root / ".shortcut-targets-by-id").is_dir():
                found.append(root)
    return found


def signed_in() -> bool:
    """An account folder exists and Drive shows a file system."""
    accounts = [p for p in drivefs_dir().glob("*") if (p / "metadata_sqlite_db").exists()] if drivefs_dir().is_dir() else []
    return bool(accounts) and bool(drive_roots())


def _databases() -> list[Path]:
    root = drivefs_dir()
    if not root.is_dir():
        return []
    return sorted(root.glob("*/metadata_sqlite_db"))


def _query(db: Path, sql: str, args=()):
    for uri in (f"file:{db}?mode=ro", f"file:{db}?mode=ro&immutable=1"):
        try:
            with sqlite3.connect(uri, uri=True, timeout=2) as conn:
                return conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            continue
    return []


def folder_id(path: Path) -> str | None:
    """Drive's id for a folder on this computer, read from Drive's own records."""
    path = Path(path)
    parts = path.parts
    if ".shortcut-targets-by-id" in parts:  # Windows/Mac shortcut targets carry the id
        i = parts.index(".shortcut-targets-by-id")
        if len(parts) == i + 3:
            return parts[i + 1]
    names = [p for p in parts if p not in ("/", "\\")]
    for db in _databases():
        candidates = _query(
            db,
            "SELECT stable_id, id FROM items WHERE local_title = ? AND is_folder = 1 AND trashed = 0",
            (path.name,),
        )
        for stable_id, item_id in candidates:
            if _ancestors_match(db, stable_id, names[:-1]):
                return item_id
    return None


def _ancestors_match(db: Path, stable_id: int, above: list[str]) -> bool:
    """Walk up Drive's tree while the titles match the path's folders, up to My Drive."""
    current = stable_id
    remaining = list(above)
    for _ in range(64):
        rows = _query(
            db,
            "SELECT i.stable_id, i.local_title, i.id FROM stable_parents p "
            "JOIN items i ON i.stable_id = p.parent_stable_id WHERE p.item_stable_id = ?",
            (current,),
        )
        if not rows:
            return False
        parent, title, item_id = rows[0]
        if item_id.startswith("0A") or title == "My Drive":  # the root of My Drive
            # "~/My Drive (me@gmail.com)/…", ".../GoogleDrive-me/My Drive/…", "G:\\My Drive\\…"
            return bool(remaining) and remaining[-1].startswith("My Drive")
        if not remaining or remaining[-1] != title:
            return False
        remaining.pop()
        current = parent
    return False


def folder_link(path: Path) -> str | None:
    found = folder_id(path)
    return FOLDER_URL.format(id=found) if found else None


def invite_text(folder_name: str, link: str | None, from_name: str = "") -> str:
    """A message to paste into a chat or an email: everything a collaborator needs."""
    where = link or f"(the “{folder_name}” folder I shared with you in Google Drive)"
    lines = [
        f"Let's make music together{f' — {from_name}' if from_name else ''}. We share songs with Session Sync:",
        "",
        f"1. Install Google Drive for desktop and sign in: {DOWNLOAD_URL}",
        f"2. Open our shared folder: {where}",
        "   At the top, click the folder name › Organize › Add shortcut › My Drive.",
        "3. Install Session Sync:",
        "   Mac (Terminal): " + INSTALL_MAC,
        "   Windows (PowerShell): " + INSTALL_WINDOWS,
        "4. Open Session Sync. It finds the shared folder by itself; if not, click",
        f"   “Connect a shared folder…” and choose “{folder_name}” in Google Drive.",
    ]
    return "\n".join(lines)


def find_shared_folders(roots: list[Path] | None = None, *, depth: int = 3, limit: int = 600) -> list[Path]:
    """Folders in Google Drive that hold Session Sync songs: the ones to connect.

    Looks a few folders deep from each Drive root, listing names only (nothing
    is downloaded), and stops after `limit` folders so a huge Drive stays quick.
    """
    from sessionsync.storage.master_folder import MasterFolderStorage

    found: list[Path] = []
    seen: set[str] = set()
    visited = 0
    queue = [(Path(r), 0) for r in (roots if roots is not None else drive_roots())]
    while queue and visited < limit:
        folder, level = queue.pop(0)
        try:
            key = str(folder.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        visited += 1
        if level and MasterFolderStorage.holds_songs(folder):
            found.append(folder)
            continue
        if level >= depth:
            continue
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        queue += [(c, level + 1) for c in children if c.name != "Trash"]
    return found  # one per real folder: the same folder seen by two paths is visited once


def uploads_pending() -> tuple[int, int] | None:
    """(files, bytes) Google Drive for desktop still has to upload from this
    computer, read from its own queue. None when that cannot be told (no Drive,
    or a version of Drive that keeps it elsewhere). Bytes are 0 when unknown."""
    files = size = 0
    known = False
    for account in (drivefs_dir().glob("*") if drivefs_dir().is_dir() else []):
        mirror = account / "mirror_sqlite.db"  # "Mirror files" mode
        if mirror.exists():
            rows = _query(mirror, "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM queued_uploads")
            rows2 = _query(mirror, "SELECT COUNT(*) FROM pending_uploads")
            if rows:
                known = True
                files += rows[0][0] + (rows2[0][0] if rows2 else 0)
                size += rows[0][1]
        stream = account / "metadata_sqlite_db"  # "Stream files" mode: changes not yet sent
        if stream.exists():
            rows = _query(stream, "SELECT COUNT(*) FROM operations")
            if rows:
                known = True
                files += rows[0][0]
    return (files, size) if known else None
