"""A repository kept in an ordinary folder.

Useful in its own right — an external drive or a NAS makes a perfectly good
remote — and it is the backend the engine's consistency rules are proven
against, without needing a network.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from sessionsync.storage.base import VersionAlreadyExistsError

REPO_METADATA_FILE = "repo.json"
MANIFESTS_DIR = "manifests"
OBJECTS_DIR = "objects"
SESSIONS_DIR = "sessions"


def manifest_filename(version: int) -> str:
    return f"v{version:06d}.json"


class LocalFolderStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in (MANIFESTS_DIR, OBJECTS_DIR, SESSIONS_DIR):
            (self.root / sub).mkdir(exist_ok=True)

    def authenticate(self) -> None:
        """A folder needs no credentials."""

    def describe(self) -> str:
        return str(self.root)

    # --- metadata ------------------------------------------------------

    def read_repo_metadata(self) -> dict | None:
        path = self.root / REPO_METADATA_FILE
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_repo_metadata(self, metadata: dict) -> None:
        self._write_atomic(
            self.root / REPO_METADATA_FILE,
            json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    # --- objects -------------------------------------------------------

    def _object_path(self, sha256: str) -> Path:
        return self.root / OBJECTS_DIR / sha256[:2] / sha256

    def upload_object(self, sha256: str, source: Path) -> None:
        dest = self._object_path(sha256)
        if dest.exists():
            return  # content-addressed: identical hash means identical bytes
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".incoming")
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, dest)  # only ever appears complete
        finally:
            tmp.unlink(missing_ok=True)

    def download_object(self, sha256: str, dest: Path) -> None:
        src = self._object_path(sha256)
        if not src.exists():
            raise FileNotFoundError(f"Object {sha256} is not in this repository")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".incoming")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def existing_objects(self, hashes: set[str]) -> set[str]:
        return {h for h in hashes if self._object_path(h).exists()}

    # --- versions ------------------------------------------------------

    def list_versions(self) -> list[int]:
        versions = []
        for path in (self.root / MANIFESTS_DIR).glob("v*.json"):
            try:
                versions.append(int(path.stem.lstrip("v")))
            except ValueError:
                continue
        return sorted(versions)

    def current_version(self) -> int:
        versions = self.list_versions()
        return versions[-1] if versions else 0

    def read_manifest(self, version: int) -> bytes:
        path = self.root / MANIFESTS_DIR / manifest_filename(version)
        if not path.exists():
            raise FileNotFoundError(f"No manifest for version {version}")
        return path.read_bytes()

    def publish_manifest(self, version: int, data: bytes) -> None:
        path = self.root / MANIFESTS_DIR / manifest_filename(version)
        try:
            # O_EXCL is the commit point: exactly one writer can win a version.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise VersionAlreadyExistsError(
                f"Version {version} has already been published by someone else"
            ) from exc
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())

    # --- session presence ----------------------------------------------

    def write_session(self, key: str, data: dict) -> None:
        self._write_atomic(
            self.root / SESSIONS_DIR / f"{key}.json",
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
        )

    def list_sessions(self) -> list[dict]:
        sessions = []
        for path in sorted((self.root / SESSIONS_DIR).glob("*.json")):
            try:
                sessions.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue  # a half-written heartbeat is not worth failing over
        return sessions

    def delete_session(self, key: str) -> None:
        (self.root / SESSIONS_DIR / f"{key}.json").unlink(missing_ok=True)

    # --- internals -----------------------------------------------------

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".incoming")
        tmp.write_bytes(data)
        os.replace(tmp, path)
