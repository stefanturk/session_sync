"""What the shared folders mean for this Mac: song rows, news, and safe auto-pulls.

No Qt in here, so all of it is unit tested. The window calls these on a timer
and just shows what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sessionsync.core.hub import Hub, HubSong, compare_scans, pull_if_safe
from sessionsync.core.localstate import LocalState
from sessionsync.core.repository import Repository
from sessionsync.ui.settings import Settings


@dataclass
class News:
    """One thing to tell the user, and where clicking it should take them."""

    text: str
    hub: Hub
    song: str
    path: Path | None  # the song's folder on this Mac, if it has one

    def __str__(self) -> str:
        return self.text


@dataclass
class SongRow:
    hub: Hub
    song: HubSong
    local: Path
    state: str  # "new" | "behind" | "current"
    behind_by: int = 0

    @property
    def label(self) -> str:
        if self.state == "new":
            return "New · not on this Mac yet"
        if self.state == "behind":
            n = self.behind_by
            return f"{n} new version{'s' if n != 1 else ''} to pull"
        return "Up to date"

    @property
    def detail(self) -> str:
        bits = [self.song.latest_author, self.song.latest_summary or self.song.latest_note or ""]
        return " · ".join(b for b in bits if b)


def song_rows(hub: Hub, settings: Settings) -> list[SongRow]:
    rows = []
    for song in hub.songs():
        local = settings.song_path(hub, song.name)
        if local.is_dir() and LocalState.exists(local):
            base = LocalState.open(local).base_version
            behind = max(0, song.latest_version - base)
            rows.append(SongRow(hub, song, local, "behind" if behind else "current", behind))
        else:
            rows.append(SongRow(hub, song, local, "new"))
    return rows


def check_hubs(settings: Settings, author: dict, open_roots: set[Path] | None = None) -> list[News]:
    """One pass over every hub. Returns the lines to tell the user about.

    Songs open in a window are left to that window (it has its own engine and
    shows its own "behind" state); closed ones are pulled when that is safe.
    """
    open_roots = {Path(p).resolve() for p in (open_roots or set())}
    messages: list[News] = []
    for hub in settings.hubs():
        events, seen = compare_scans(hub, settings.seen(hub), me=author)
        settings.set_seen(hub, seen)
        for event in events:
            if event.kind == "new_song":
                messages.append(News(f"{hub.name} added a new song: {event.song}", hub, event.song, None))
                continue
            pulled = _auto_pull(hub, event.song, settings, author, open_roots)
            who = event.authors[-1]
            what = " · ".join(dict.fromkeys(event.summaries)) or "new version"
            tail = " (updated on this Mac)" if pulled else ""
            local = settings.song_path(hub, event.song)
            messages.append(
                News(f"{who} on {event.song}: {what}{tail}", hub, event.song, local if local.is_dir() else None)
            )
    return messages


def _auto_pull(hub: Hub, song: str, settings: Settings, author: dict, open_roots: set[Path]) -> bool:
    local = settings.song_path(hub, song)
    if not local.is_dir() or not LocalState.exists(local) or local.resolve() in open_roots:
        return False
    try:
        repo = Repository.open(local, author=author, storage=hub.storage_for(song))
        return pull_if_safe(repo) is not None
    except Exception:  # a failed background pull is retried next minute, never fatal
        return False


def join(hub: Hub, song: str, settings: Settings, author: dict) -> Path:
    repo = hub.join_song(song, settings.local_root(), author)
    settings.set_song_path(hub, song, repo.project_root)
    return repo.project_root


def share(hub: Hub, project: Path, settings: Settings, author: dict, name: str | None = None) -> Path:
    song_name = name or Path(project).name
    hub.add_song(song_name, project, author)
    settings.set_song_path(hub, song_name, Path(project))
    _, seen = compare_scans(hub, None)
    previous = settings.seen(hub) or {}
    settings.set_seen(hub, {**previous, song_name: seen.get(song_name, 1)})
    return Path(project)
