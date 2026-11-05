"""Storage backends, addressed by location string."""

from __future__ import annotations

from pathlib import Path

from sessionsync.storage.base import StorageProvider, VersionAlreadyExistsError
from sessionsync.storage.local_folder import LocalFolderStorage
from sessionsync.storage.master_folder import MasterFolderStorage


def storage_from_location(location: str) -> StorageProvider:
    """Build the right backend for a stored remote location.

    Plain paths are folder repositories; `gdrive://<folder id>` will be Google
    Drive once that backend lands.
    """
    if location.startswith("gdrive://"):
        raise NotImplementedError("Google Drive storage is not available yet.")
    if location.startswith(MasterFolderStorage.PREFIX):
        return MasterFolderStorage(Path(location[len(MasterFolderStorage.PREFIX):]))
    return LocalFolderStorage(Path(location))


__all__ = [
    "LocalFolderStorage",
    "MasterFolderStorage",
    "StorageProvider",
    "VersionAlreadyExistsError",
    "storage_from_location",
]
