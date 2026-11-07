"""Best-effort: is Ableton Live open on this song right now?

Live writes `Loading document "<path>.als"` to its Log.txt every time a set is
opened, but never says when one is closed. So "open" means: Live is running and
the last set it loaded lives inside the project folder. It can be wrong (Live
may have switched to an unsaved new set without logging), which is why this
only ever drives gentle notes, never a lock.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from sessionsync.core import osinfo


def _prefs() -> Path:
    """Where Live keeps its Log.txt: one folder per Live version under here."""
    if osinfo.IS_WINDOWS:
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(roaming) / "Ableton"
    return Path.home() / "Library" / "Preferences" / "Ableton"


PREFS = _prefs()
# Mac: Live 12.1/Log.txt.  Windows: Live 12.1\Preferences\Log.txt.
_LOG_PATTERNS = ("Live */Log.txt", "Live */Preferences/Log.txt")
_LOADING = re.compile(
    r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?): info: Loading document "(.+)"\s*$'
)
_TAIL_BYTES = 400_000


def parse_loading(line: str) -> tuple[datetime, Path] | None:
    match = _LOADING.match(line.rstrip("\n"))
    if not match:
        return None
    return datetime.fromisoformat(match.group(1)), Path(match.group(2))


def newest_log(prefs: Path | None = None) -> Path | None:
    base = Path(prefs or PREFS)
    logs = [p for pattern in _LOG_PATTERNS for p in base.glob(pattern) if p.is_file()]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def last_document(log: Path) -> tuple[datetime, Path] | None:
    """The most recent set Live loaded, from the tail of its log.

    A long session can push the "Loading document" line out of the tail; then
    the whole log is read (Live starts a new one each launch), because missing
    it would look like the song had been closed.
    """
    try:
        with log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            found = _last_loading(fh.read())
            if found is None and size > _TAIL_BYTES:
                fh.seek(0)
                found = _last_loading(fh.read())
    except OSError:
        return None
    return found


def _last_loading(data: bytes) -> tuple[datetime, Path] | None:
    found = None
    for line in data.decode("utf-8", errors="replace").splitlines():
        parsed = parse_loading(line)
        if parsed:
            found = parsed
    return found


def live_is_running() -> bool:
    if osinfo.IS_WINDOWS:
        return _live_in_tasklist()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "Ableton Live"], capture_output=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _live_in_tasklist() -> bool:
    """Windows: Live runs as "Ableton Live 12 Suite.exe" and the like."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10,
            **osinfo.quiet_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return running_in_tasklist(result.stdout.decode("utf-8", errors="replace"))


def running_in_tasklist(text: str) -> bool:
    for line in text.splitlines():
        name = line.split(",", 1)[0].strip().strip('"').lower()
        if name.startswith("ableton live") and name.endswith(".exe"):
            return True
    return False


def open_document(*, log: Path | None = None, running: bool | None = None) -> Path | None:
    """The set Live most likely has open, or None."""
    if running is None:
        running = live_is_running()
    if not running:
        return None
    log = log or newest_log()
    if log is None:
        return None
    found = last_document(log)
    return found[1] if found else None


def is_open_in(folder: Path, document: Path | None) -> bool:
    if document is None:
        return False
    try:
        document.resolve().relative_to(Path(folder).resolve())
    except ValueError:
        return False
    return True
