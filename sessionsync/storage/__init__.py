"""Storage backends, addressed by location string."""

from __future__ import annotations

from pathlib import Path

from sessionsync.storage.base import StorageProvider, VersionAlreadyExistsError
from sessionsync.storage.local_folder import LocalFolderStorage


def storage_from_location(location: str) -> StorageProvider:
    """Build the right backend for a stored remote location.

    Plain paths are folder repositories; `gdrive://<folder id>` will be Google
    Drive once that backend lands.
    """
    if location.startswith("gdrive://"):
        raise NotImplementedError("Google Drive storage is not available yet.")
    return LocalFolderStorage(Path(location))


__all__ = [
    "LocalFolderStorage",
    "StorageProvider",
    "VersionAlreadyExistsError",
    "storage_from_location",
]
