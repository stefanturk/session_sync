"""A hub: one shared folder per collaborator, holding many songs.

Each song is a subfolder holding an Ableton project - the shared copy. A song
SessionSync already looks after has a hidden `.sessionsync/` inside it; a plain
Ableton project dropped in is listed as "ready to add". Anything else in the hub
(bounces, stems, documents) is not SessionSync's business and is ignored. A hub is
just a folder, so it works with whatever keeps it in step between computers
(Google Drive for desktop).

Scanning never writes: on a synced folder, even creating an empty directory
would be uploaded and show up on everyone else's Mac.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core import master
from sessionsync.core.repository import Repository
from sessionsync.core.summary import describe_version
from sessionsync.core.manifest import Manifest
from sessionsync.storage.master_folder import MasterFolderStorage


@dataclass
class HubSong:
    name: str
    path: Path
    kind: str = "shared"  # "shared" (looked after) | "ready" (plain project, not added yet)
    latest_version: int = 0
    latest_author: str = ""
    latest_time: str | None = None
    latest_summary: str = ""
    latest_note: str | None = None
    people: list = field(default_factory=list)  # presence notes left by collaborators


@dataclass
class HubEvent:
    """Something worth telling the user, from comparing two scans of a hub."""

    kind: str  # "new_song" | "new_versions"
    song: str
    versions: list[int] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)


class Hub:
    def __init__(self, path: Path, name: str | None = None):
        self.path = Path(path)
        self.name = name or self.path.name

    # --- looking ---------------------------------------------------------

    def songs(self) -> list[HubSong]:
        if not self.path.is_dir():
            return []
        adapter = AbletonAdapter()
        found = []
        for folder in sorted(self.path.iterdir(), key=lambda p: p.name.lower()):
            if folder.name.startswith(".") or not folder.is_dir():
                continue
            if MasterFolderStorage.looks_like_master(folder):
                found.append(self._shared_song(folder))
            elif adapter.detect_project(folder):
                found.append(HubSong(name=folder.name, path=folder, kind="ready"))
        return found

    @staticmethod
    def _shared_song(folder: Path) -> HubSong:
        storage = MasterFolderStorage(folder)
        versions = storage.list_versions()
        song = HubSong(name=folder.name, path=folder, latest_version=versions[-1] if versions else 0)
        try:
            song.people = storage.list_sessions()
            if versions:
                manifest = Manifest.from_json(storage.read_manifest(versions[-1]))
                author = manifest.author or {}
                song.latest_author = author.get("name") or author.get("email") or ""
                song.latest_time = manifest.created_at
                song.latest_note = manifest.note
                song.latest_summary = describe_version(None, manifest) if manifest.summary else ""
        except (OSError, ValueError, KeyError):
            pass  # half-synced by Drive; the next scan will see it whole
        return song

    def song(self, name: str) -> HubSong | None:
        return next((s for s in self.songs() if s.name == name), None)

    def local_path(self, song_name: str, local_base: Path) -> Path:
        return Path(local_base) / self.name / song_name

    def storage_for(self, song_name: str) -> MasterFolderStorage:
        return MasterFolderStorage(self.path / song_name)

    # --- acting ----------------------------------------------------------

    def add_song(self, song_name: str, project: Path, author: dict, *, note: str | None = None) -> Repository:
        """Copy a project that lives on this Mac into the hub; it becomes the working copy."""
        return master.add_local_project(Path(project), self.path, author=author, name=song_name)

    def join_song(self, song_name: str, local_base: Path, author: dict, destination: Path | None = None) -> Repository:
        """Get your own working copy of a shared song.

        A plain project sitting in the hub is set up first (only the hidden
        `.sessionsync` folder is written there; the project files are untouched).
        """
        song = self.song(song_name)
        if song is None:
            from sessionsync.core.errors import NotAProjectError

            raise NotAProjectError(f"{self.name} has no song called '{song_name}'.")
        if song.kind == "ready":
            master.adopt(song.path, author=author)
        return master.get_workbench(
            song.path, destination or self.local_path(song_name, local_base), author=author
        )


def compare_scans(
    hub: Hub, previous: dict[str, int] | None, me: dict | None = None
) -> tuple[list[HubEvent], dict[str, int]]:
    """Events since `previous` ({song: latest version seen}) and the new seen map.

    `previous` of None means this hub was just connected: everything already
    there is the starting point, not news. Versions pushed by `me` (matched by
    email) are not news either, though they still advance the seen map.
    """
    songs = hub.songs()
    current = {s.name: s.latest_version for s in songs}
    if previous is None:
        return [], current

    events: list[HubEvent] = []
    for song in songs:
        before = previous.get(song.name)
        if before is None:
            events.append(HubEvent("new_song", song.name, [song.latest_version]))
        elif song.latest_version > before:
            storage = hub.storage_for(song.name)
            versions, summaries, authors = [], [], []
            reached = before
            prior = Manifest.from_json(storage.read_manifest(before)) if before else None
            for v in range(before + 1, song.latest_version + 1):
                try:
                    manifest = Manifest.from_json(storage.read_manifest(v))
                except (OSError, ValueError, KeyError):
                    break  # a version still arriving from Drive; catch it next time
                who = manifest.author or {}
                reached = v
                mine = bool(me and who.get("email") and who.get("email") == me.get("email"))
                if not mine:
                    versions.append(v)
                    summaries.append(describe_version(prior, manifest))
                    authors.append(who.get("name") or who.get("email") or "Someone")
                prior = manifest
            current[song.name] = reached
            if versions:
                events.append(HubEvent("new_versions", song.name, versions, summaries, authors))
    return events, current


def pull_if_safe(repo: Repository):
    """Pull the newest version only if it cannot disturb anything of yours.

    Safe means: behind the hub, nothing edited here since the last sync, and no
    session of yours open on the song. Otherwise returns None and the caller
    shows "Sam made 2 new versions" with a Pull button instead.
    """
    if repo.state.session:
        return None
    if repo.status().state != "behind":
        return None
    return repo.pull()


def hub_to_json(hub: Hub) -> dict:
    return {"name": hub.name, "path": str(hub.path)}


__all__ = ["Hub", "HubEvent", "HubSong", "compare_scans", "hub_to_json", "pull_if_safe"]
