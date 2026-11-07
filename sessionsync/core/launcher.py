"""The front door of a song folder in Drive: "▶ Open in Live".

The shared copy must never be the thing someone opens in Live, so the top of
each song folder holds a link instead. Double-clicking it hands
`sessionsync://open?repo=<id>&song=<name>` to Session Sync, which opens *your*
copy of the song (pulling it first if it needs to).

A link file, not a script: Google Drive does not keep the "executable" bit, so
a `.command` would arrive on a collaborator's Mac as a file that refuses to
run. A `.webloc` is plain data, and the repository id inside it is the same for
everybody even though everyone's Drive folder is somewhere different.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

SCHEME = "sessionsync"
MAC_LAUNCHER = "▶ Open in Live.webloc"
#: Windows hides the .url ending, so this shows as "▶ Open in Live (Windows)".
WINDOWS_LAUNCHER = "▶ Open in Live (Windows).url"
READ_ME = "Read me.txt"
FRONT_DOOR = (MAC_LAUNCHER, WINDOWS_LAUNCHER, READ_ME)

READ_ME_TEXT = """\
This folder is the SHARED copy of "{song}". Please don't open anything in here.

To work on the song, double-click "▶ Open in Live" (on a Mac) or
"▶ Open in Live (Windows)" (on a PC). Session Sync opens YOUR own copy (on your
computer, not in Drive), and gets the latest version first.

No Session Sync yet? Ask whoever shared this folder with you for the app.
"""


def link_for(repository_id: str, song: str) -> str:
    return f"{SCHEME}://open?repo={quote(repository_id)}&song={quote(song)}"


def parse_link(url: str) -> tuple[str, str] | None:
    """(repository id, song name) from a launcher link, or None if it is not one."""
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    if parsed.scheme != SCHEME or parsed.netloc != "open":
        return None
    query = parse_qs(parsed.query)
    repo = (query.get("repo") or [""])[0]
    if not repo:
        return None
    return repo, (query.get("song") or [""])[0]


def is_front_door(name: str) -> bool:
    return name in FRONT_DOOR


def write_front_door(song_folder: Path, repository_id: str, song: str) -> None:
    """Write the launcher and read-me, only when they are missing or wrong.

    Rewriting an identical file would still make Drive upload it and notify
    every collaborator's Mac, so unchanged files are left alone.
    """
    song_folder = Path(song_folder)
    _write_if_changed(
        song_folder / MAC_LAUNCHER,
        plistlib.dumps({"URL": link_for(repository_id, song)}),
    )
    _write_if_changed(song_folder / WINDOWS_LAUNCHER, windows_shortcut(link_for(repository_id, song)))
    _write_if_changed(song_folder / READ_ME, READ_ME_TEXT.format(song=song).encode("utf-8"))


def windows_shortcut(url: str) -> bytes:
    """An Internet Shortcut: what Windows makes when you drag a link to the Desktop."""
    return f"[InternetShortcut]\r\nURL={url}\r\n".encode("ascii")


def read_link(path: Path) -> str | None:
    """The link inside either kind of launcher file."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if Path(path).suffix == ".webloc":
        try:
            return plistlib.loads(data).get("URL")
        except Exception:  # noqa: BLE001 - a damaged file is just "no link"
            return None
    for line in data.decode("utf-8", errors="replace").splitlines():
        if line.startswith("URL="):
            return line[4:].strip()
    return None


def _write_if_changed(path: Path, data: bytes) -> None:
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    tmp = path.with_name(path.name + ".incoming")
    tmp.write_bytes(data)
    tmp.replace(path)
