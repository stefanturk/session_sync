"""Starting points for the shared-copy workflow.

Three situations, all ending in the same place (a shared copy in Drive and a
working copy anywhere):

- the project already sits in the Drive folder  -> `adopt` it, then `get_workbench`
- the project only exists on this Mac           -> `add_local_project`
- you want your own copy of a shared one        -> `get_workbench`
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.errors import NotAProjectError
from sessionsync.core.hashing import HashCache
from sessionsync.core.localstate import LocalState
from sessionsync.core.manifest import Manifest, build_file_map
from sessionsync.core.repository import REPOSITORY_FORMAT_VERSION, Repository
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
        "repository_format_version": REPOSITORY_FORMAT_VERSION,
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
    """Put a project that only exists on this Mac into the shared folder.

    The local folder becomes the working copy. Nothing is created in the
    shared folder unless the project is valid and the name is free.
    """
    local_folder, hub_folder = Path(local_folder), Path(hub_folder)
    adapter = AbletonAdapter()
    if not adapter.detect_project(local_folder):
        raise NotAProjectError(
            f"{local_folder.name} does not look like an Ableton project folder. "
            "Choose the folder that contains the Live Set (.als)."
        )
    target = hub_folder / (name or local_folder.name)
    if target.exists():
        raise FileExistsError(
            f"The shared folder already has '{target.name}'. If it is the same song, "
            "use Get it instead."
        )
    try:
        repo = Repository.create(local_folder, MasterFolderStorage(target), adapter=adapter, author=author)
        repo.push(note="Added to the shared folder")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(local_folder / ".sessionsync", ignore_errors=True)
        raise
    return repo


def _tracks(adapter, folder):
    try:
        return adapter.track_fingerprints(folder)
    except Exception:
        return None


def _added_all(files):
    from sessionsync.core.manifest import diff_file_maps

    return diff_file_maps({}, files)
