"""The storage provider interface.

Storage is a dumb content-addressed store plus an append-only set of numbered
manifests. It knows nothing about Ableton, sessions or diffing.

The one rule with teeth: `publish_manifest` must refuse a version that already
exists. That refusal is what makes "first push wins" true, and it is why a
stale push can never overwrite someone else's work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """Something went wrong talking to the storage backend."""


class VersionAlreadyExistsError(StorageError):
    """Someone else published this version first."""


@runtime_checkable
class StorageProvider(Protocol):
    def authenticate(self) -> None: ...

    # repository metadata
    def read_repo_metadata(self) -> dict | None: ...
    def write_repo_metadata(self, metadata: dict) -> None: ...

    # content-addressed objects
    def upload_object(self, sha256: str, source: Path) -> None: ...
    def download_object(self, sha256: str, dest: Path) -> None: ...
    def existing_objects(self, hashes: set[str]) -> set[str]: ...

    # versions
    def current_version(self) -> int: ...
    def list_versions(self) -> list[int]: ...
    def read_manifest(self, version: int) -> bytes: ...
    def publish_manifest(self, version: int, data: bytes) -> None: ...

    # advisory presence
    def write_session(self, key: str, data: dict) -> None: ...
    def list_sessions(self) -> list[dict]: ...
    def delete_session(self, key: str) -> None: ...
