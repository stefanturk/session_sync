"""A hub: one shared folder per collaborator, holding many songs.

Each song is a subfolder that contains a SessionSync repository (it has a
`repo.json`). Anything else in the hub - bounces, stems, documents - is not
SessionSync's business and is ignored. A hub is just a folder, so it works with
whatever keeps that folder in step between computers (Google Drive for desktop).

Scanning never writes: on a synced folder, even creating an empty directory
would be uploaded and show up on everyone else's Mac.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sessionsync.adapters import get_adapter
from sessionsync.core.errors import NotAProjectError
from sessionsync.core.repository import Repository
from sessionsync.core.summary import describe_version
from sessionsync.core.manifest import Manifest
from sessionsync.storage.local_folder import LocalFolderStorage

REPO_FILE = "repo.json"
_MANIFEST_RE = re.compile(r"^v(\d{6})\.json$")


@dataclass
class HubSong:
    name: str
    path: Path
    latest_version: int = 0
    latest_author: str = ""
    latest_time: str | None = None
    latest_summary: str = ""
    latest_note: str | None = None


@dataclass
class HubEvent:
    """Something worth telling the user, from comparing two scans of a hub."""

    kind: str  # "new_song" | "new_versions"
    song: str
    versions: list[int] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)


def _latest_manifest(manifests_dir: Path) -> tuple[int, Manifest | None]:
    versions = sorted(
        int(m.group(1))
        for p in manifests_dir.iterdir()
        if (m := _MANIFEST_RE.match(p.name))
    ) if manifests_dir.is_dir() else []
    if not versions:
        return 0, None
    try:
        return versions[-1], Manifest.from_json((manifests_dir / f"v{versions[-1]:06d}.json").read_bytes())
    except (OSError, ValueError, KeyError):
        return versions[-1], None  # half-synced by Drive; the next scan will see it whole


class Hub:
    def __init__(self, path: Path, name: str | None = None):
        self.path = Path(path)
        self.name = name or self.path.name

    # --- looking ---------------------------------------------------------

    def songs(self) -> list[HubSong]:
        if not self.path.is_dir():
            return []
        found = []
        for folder in sorted(self.path.iterdir(), key=lambda p: p.name.lower()):
            if folder.name.startswith(".") or not (folder / REPO_FILE).is_file():
                continue
            version, manifest = _latest_manifest(folder / "manifests")
            song = HubSong(name=folder.name, path=folder, latest_version=version)
            if manifest is not None:
                author = manifest.author or {}
                song.latest_author = author.get("name") or author.get("email") or ""
                song.latest_time = manifest.created_at
                song.latest_note = manifest.note
                song.latest_summary = describe_version(None, manifest) if manifest.summary else ""
            found.append(song)
        return found

    def song(self, name: str) -> HubSong | None:
        return next((s for s in self.songs() if s.name == name), None)

    def local_path(self, song_name: str, local_base: Path) -> Path:
        return Path(local_base) / self.name / song_name

    def storage_for(self, song_name: str) -> LocalFolderStorage:
        return LocalFolderStorage(self.path / song_name)

    # --- acting ----------------------------------------------------------

    def add_song(self, song_name: str, project: Path, author: dict, *, note: str | None = None) -> Repository:
        """Put an existing local Ableton project into this hub and push version 1."""
        target = self.path / song_name
        if target.exists():
            raise FileExistsError(f"{self.name} already has a song called '{song_name}'.")
        repo = Repository.create(
            Path(project),
            LocalFolderStorage(target),
            adapter=get_adapter("ableton"),
            author=author,
            project_name=song_name,
        )
        repo.push(note=note or "First version")
        return repo

    def join_song(self, song_name: str, local_base: Path, author: dict) -> Repository:
        """Download a song from this hub onto this Mac."""
        song = self.song(song_name)
        if song is None:
            raise NotAProjectError(f"{self.name} has no song called '{song_name}'.")
        destination = self.local_path(song_name, local_base)
        if destination.exists() and any(destination.iterdir()):
            raise FileExistsError(f"{destination} already has files in it.")
        return Repository.clone(self.storage_for(song_name), destination, author=author)


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
