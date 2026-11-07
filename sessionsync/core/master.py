"""Starting points for the shared-copy workflow.

Three situations, all ending in the same place (a shared copy in Drive and a
working copy anywhere):

- the project already sits in the Drive folder  -> `adopt` it, then `get_workbench`
- the project only exists on this computer           -> `add_local_project`
- you want your own copy of a shared one        -> `get_workbench`
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.errors import NotAProjectError, PartOfASongError
from sessionsync.core.hashing import HashCache
from sessionsync.core.localstate import LocalState
from sessionsync.core.manifest import Manifest, build_file_map
from sessionsync.core.repository import Repository
from sessionsync.core.summary import summarise
from sessionsync.storage.master_folder import MasterFolderStorage


def folder_kind(folder: Path) -> str:
    """'master' (the shared copy), 'workbench' (a working copy) or 'unmanaged'."""
    folder = Path(folder)
    if LocalState.exists(folder):
        return "workbench"
    if MasterFolderStorage.looks_like_master(folder):
        return "master"
    return "unmanaged"


def adopt(folder: Path, *, author: dict, adapter: AbletonAdapter | None = None) -> Manifest:
    """Make an existing project folder in Drive the shared copy, in place.

    Only the hidden `.sessionsync/` folder is written. The project files are
    read to fingerprint them and are never moved, copied or modified.
    """
    folder = Path(folder)
    adapter = adapter or AbletonAdapter()
    refuse_part_of_a_song(folder)
    if not adapter.detect_project(folder):
        raise NotAProjectError(
            f"{folder.name} does not look like an Ableton project folder. "
            "Choose the folder that contains the Live Set (.als)."
        )
    if folder_kind(folder) != "unmanaged":
        raise FileExistsError(f"{folder.name} is already set up.")

    storage = MasterFolderStorage(folder)
    with tempfile.TemporaryDirectory() as scratch:
        cache = HashCache(Path(scratch) / "hashes.db")
        try:
            files = build_file_map(folder, adapter.enumerate_tracked_files(folder), cache)
        finally:
            cache.close()
    tracks = _tracks(adapter, folder)
    metadata = {
        # The files are where the person put them (format 1) until `migrate`
        # below moves them into the format-2 layout.
        "repository_format_version": 1,
        "repository_id": str(uuid.uuid4()),
        "project_name": folder.name,
        "project_format": adapter.format_name,
        "project_adapter_version": adapter.adapter_version,
        "created_by": author,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    manifest = Manifest(
        version=1,
        repository_id=metadata["repository_id"],
        parent_version=None,
        project_format=adapter.format_name,
        author=author,
        note="Added to the shared folder",
        files=files,
        summary=summarise(_added_all(files), None, tracks, first=True),
        tracks=tracks,
    )
    storage.write_repo_metadata(metadata)
    storage.publish_manifest(1, manifest.to_json().encode("utf-8"))
    storage.migrate()
    return manifest


def get_workbench(master_folder: Path, destination: Path, *, author: dict) -> Repository:
    """Copy the shared project to `destination` and link it as a working copy."""
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"{destination} already has files in it; choose a new folder.")
    created = not destination.exists()
    try:
        return Repository.clone(MasterFolderStorage(master_folder), destination, author=author)
    except BaseException:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def add_local_project(
    local_folder: Path, hub_folder: Path, *, author: dict, name: str | None = None
) -> Repository:
    """Put a project that only exists on this computer into the shared folder.

    The local folder becomes the working copy. Nothing is created in the
    shared folder unless the project is valid and the name is free.
    """
    local_folder, hub_folder = Path(local_folder), Path(hub_folder)
    adapter = AbletonAdapter()
    if MasterFolderStorage.song_around(local_folder) is not None or MasterFolderStorage.holds_songs(local_folder):
        raise PartOfASongError(
            f"{local_folder.name} is (or is inside) a shared song in Google Drive, not a "
            "project on this computer. To work on that song, open it from the list instead."
        )
    if _inside(local_folder, hub_folder) or _inside(hub_folder, local_folder):
        raise PartOfASongError(
            f"{local_folder.name} is inside the shared folder. Choose the project on this "
            "computer that you want to share (for example on your Desktop or in Music)."
        )
    if (local_folder / ".sessionsync").exists():
        raise FileExistsError(
            f"{local_folder.name} is already linked to a shared song. Open it instead."
        )
    if not adapter.detect_project(local_folder):
        raise NotAProjectError(
            f"{local_folder.name} does not look like an Ableton project folder. "
            "Choose the folder that contains the Live Set (.als)."
        )
    target = hub_folder / (name or local_folder.name)
    if target.exists():
        raise FileExistsError(
            f"The shared folder already has '{target.name}'. If it is the same song, "
            "use Pull instead."
        )
    try:
        repo = Repository.create(local_folder, MasterFolderStorage(target), adapter=adapter, author=author)
        repo.push(note="Added to the shared folder")
    except BaseException:
        # Only what this call created: `target` and the `.sessionsync` were
        # checked above not to exist. Never anything that was already there.
        shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(local_folder / ".sessionsync", ignore_errors=True)
        raise
    return repo


def refuse_part_of_a_song(folder: Path) -> None:
    """Never set up a song inside a song (its data folder, Live's Backup), or a shared folder."""
    around = MasterFolderStorage.song_around(folder)
    if around is not None and around != Path(folder):
        raise PartOfASongError(
            f"{Path(folder).name} is part of the shared song {around.name}, not a song of "
            "its own. Nothing was changed."
        )
    if MasterFolderStorage.holds_songs(folder):
        raise PartOfASongError(
            f"{Path(folder).name} holds shared songs: it is a shared folder, not a song. "
            "Nothing was changed."
        )


def _inside(path: Path, other: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(other).resolve())
        return True
    except (ValueError, OSError):
        return False


def _tracks(adapter, folder):
    try:
        return adapter.track_fingerprints(folder)
    except Exception:
        return None


def _added_all(files):
    from sessionsync.core.manifest import diff_file_maps

    return diff_file_maps({}, files)
