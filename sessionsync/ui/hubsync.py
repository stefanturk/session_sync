"""What the shared folders mean for this Mac: song rows, news, and safe auto-pulls.

No Qt in here, so all of it is unit tested. The window calls these on a timer
and just shows what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    state: str  # "ready" | "new" | "behind" | "current"
    behind_by: int = 0
    working: list = field(default_factory=list)  # names of others at it right now
    unpushed: list = field(default_factory=list)  # names of others with changes not pushed

    @property
    def label(self) -> str:
        text = self._base_label
        if self.unpushed and not self.working and self.state != "current":
            text += f" · {', '.join(self.unpushed)} has changes not pushed"
        return text

    @property
    def _base_label(self) -> str:
        if self.working:
            return f"{', '.join(self.working)} is working on it now"
        if self.state == "ready":
            return "Ready to add · Pull"
        if self.state == "new":
            return "Not on this Mac yet · Pull"
        if self.state == "behind":
            n = self.behind_by
            return f"{n} newer version{'s' if n != 1 else ''} to get"
        if self.unpushed:
            return f"{', '.join(self.unpushed)} has changes not pushed"
        return "Up to date"

    @property
    def detail(self) -> str:
        bits = [self.song.latest_author, self.song.latest_summary or self.song.latest_note or ""]
        return " · ".join(b for b in bits if b)


def song_rows(hub: Hub, settings: Settings, me: dict | None = None) -> list[SongRow]:
    rows = []
    for song in hub.songs():
        local = settings.song_path(hub, song.name)
        if local.is_dir() and LocalState.exists(local):
            base = LocalState.open(local).base_version
            behind = max(0, song.latest_version - base)
            row = SongRow(hub, song, local, "behind" if behind else "current", behind)
        else:
            row = SongRow(hub, song, local, "ready" if song.kind == "ready" else "new")
        row.working, row.unpushed = others_on(song, me)
        rows.append(row)
    return rows


def others_on(song: HubSong, me: dict | None) -> tuple[list[str], list[str]]:
    """Who else is at this song right now, and who has changes they have not pushed."""
    from sessionsync.core.session import ACTIVE_AFTER_SECONDS, UNPUSHED_STAYS_FOR_SECONDS, _parse
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    working, unpushed = [], []
    mine = (me or {}).get("email")
    for person in song.people:
        if mine and person.get("user") == mine:
            continue
        beat = _parse(person.get("last_heartbeat"))
        if beat is None:
            continue
        name = person.get("display_name") or "Someone"
        if person.get("state") == "unpushed":
            if now - beat <= timedelta(seconds=UNPUSHED_STAYS_FOR_SECONDS):
                unpushed.append(name)
        elif now - beat <= timedelta(seconds=ACTIVE_AFTER_SECONDS):
            working.append(name)
    return working, unpushed


def live_warning(settings: Settings, author: dict, document: Path | None) -> News | None:
    """Live just opened a set in a shared song: say if that is a bad idea.

    Best effort: it needs this app running, and Drive can be a minute behind.
    """
    from sessionsync.core import livewatch
    from sessionsync.core.master import folder_kind

    if document is None:
        return None
    for hub in settings.hubs():
        for song in hub.songs():
            local = settings.song_path(hub, song.name)
            if livewatch.is_open_in(song.path, document):
                return News(
                    f"You opened the shared copy of {song.name} directly. Work on your own "
                    "copy instead (Pull), or Live will change what everyone else gets.",
                    hub, song.name, None,
                )
            if local.is_dir() and livewatch.is_open_in(local, document):
                working, _ = others_on(song, author)
                if working:
                    return News(
                        f"{', '.join(working)} is working on {song.name} right now. "
                        "Call each other before you both record.",
                        hub, song.name, local,
                    )
    return None


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


def join(hub: Hub, song: str, settings: Settings, author: dict, destination: Path | None = None) -> Path:
    repo = hub.join_song(song, settings.local_root(), author, destination)
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
