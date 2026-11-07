"""The shared copy: an ordinary Ableton project folder with bookkeeping inside.

The project files sit in the folder exactly as Live wants them, so the shared
copy opens in Live and looks like any project in Google Drive. A hidden
`.sessionsync/` folder holds the numbered manifests, the old content of files
that were overwritten (the history, deltas only), presence and a staging area.

Nothing a revision used is ever thrown away:

- A file a later revision no longer uses stays exactly where it is, so every
  set in `Past Revisions/` still opens straight from the shared folder.
- A file overwritten with new content keeps its old bytes in the history.
- Each push saves its main Live Set into `Past Revisions/`.

Nobody works in this folder. People work on their own copy and put it back:

- `upload_object` only copies into staging, so the visible project is untouched
  while a push is being prepared.
- `publish_manifest` is the commit point. It creates the manifest exclusively
  (one winner per version), then swaps the visible files for the new ones. The
  swap is idempotent, so a crash at any step is finished — or undone — by the
  next client. A version counts only once its `.committed` marker exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from sessionsync.core.hashing import hash_file
from sessionsync.core.manifest import FileEntry, Manifest
from sessionsync.storage.base import NotReadyError, StorageError, VersionAlreadyExistsError

META_DIR = ".sessionsync"
REPO_FILE = "repo.json"
RETENTION_FILE = "retention.json"
MANIFESTS_DIR = "manifests"
HISTORY_DIR = "history"
PRESENCE_DIR = "presence"
STAGING_DIR = "staging"
LOCK_FILE = "commit.lock"

# A push that has been silent this long has certainly died.
STALE_COMMIT_SECONDS = 10 * 60
_CHUNK = 1024 * 1024


class MasterFolderStorage:
    PREFIX = "master:"

    def __init__(self, root: Path):
        self.root = Path(root)
        self._index_cache: tuple[int, dict[str, list[str]]] | None = None
        self._all_index_cache: tuple[int, dict[str, list[tuple[str, int]]]] | None = None

    # --- locations -----------------------------------------------------

    @property
    def meta(self) -> Path:
        return self.root / META_DIR

    def _manifest_path(self, version: int) -> Path:
        return self.meta / MANIFESTS_DIR / f"v{version:06d}.json"

    def _marker_path(self, version: int) -> Path:
        return self.meta / MANIFESTS_DIR / f"v{version:06d}.committed"

    def _history_path(self, sha256: str) -> Path:
        return self.meta / HISTORY_DIR / "objects" / sha256[:2] / sha256

    def _staged_path(self, sha256: str) -> Path:
        return self.meta / STAGING_DIR / sha256[:2] / sha256

    def _visible(self, rel_path: str) -> Path:
        target = (self.root / rel_path).resolve()
        if self.root.resolve() not in target.parents:
            raise StorageError(f"Refusing a path outside the project: {rel_path}")
        return self.root / rel_path

    def _ensure_layout(self) -> None:
        for sub in (MANIFESTS_DIR, PRESENCE_DIR):
            (self.meta / sub).mkdir(parents=True, exist_ok=True)

    def authenticate(self) -> None:
        """A folder needs no credentials."""

    def describe(self) -> str:
        return f"{self.PREFIX}{self.root}"

    @classmethod
    def looks_like_master(cls, folder: Path) -> bool:
        meta = Path(folder) / META_DIR
        return (meta / REPO_FILE).exists() and not (meta / "state.db").exists()

    # --- metadata ------------------------------------------------------

    def read_repo_metadata(self) -> dict | None:
        path = self.meta / REPO_FILE
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_repo_metadata(self, metadata: dict) -> None:
        self._ensure_layout()
        _write_atomic(
            self.meta / REPO_FILE,
            json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    # --- objects -------------------------------------------------------

    def upload_object(self, sha256: str, source: Path) -> None:
        dest = self._staged_path(sha256)
        if dest.exists():
            return
        _copy_verified(Path(source), dest, sha256)

    def download_object(self, sha256: str, dest: Path) -> None:
        source = self._find_source(sha256)
        if source is not None:
            try:
                _copy_verified(source, Path(dest), sha256)
                return
            except NotReadyError:
                if self._current_index().get(sha256):
                    raise  # a file of the current version is still arriving
        # an older revision's file, still sitting where that revision had it
        for older in self._older_sources(sha256):
            try:
                _copy_verified(older, Path(dest), sha256)
                return
            except NotReadyError:
                continue
        if source is not None:
            raise NotReadyError(f"{source.name} is not fully there yet. Try again in a minute.")
        raise FileNotFoundError(f"Object {sha256} is not in this shared copy")

    def existing_objects(self, hashes: set[str]) -> set[str]:
        index = self._current_index()
        present = set()
        for sha in hashes:
            if self._history_path(sha).exists() or self._staged_path(sha).exists():
                present.add(sha)
            elif any(self._visible(p).exists() for p in index.get(sha, [])):
                present.add(sha)
            elif next(self._older_sources(sha), None) is not None:
                present.add(sha)
        return present

    def _older_sources(self, sha256: str):
        """Visible files that some earlier revision held with this content.

        Only the size is checked here; every copy out of the shared folder is
        verified by hash anyway, so a file changed by hand is never trusted.
        """
        for rel, size in self._all_index().get(sha256, []):
            visible = self._visible(rel)
            if _has_size(visible, size):
                yield visible

    def _all_index(self) -> dict[str, list[tuple[str, int]]]:
        """{hash: [(path, size), ...]} across every committed revision, newest first."""
        version = self._committed_version()
        if self._all_index_cache and self._all_index_cache[0] == version:
            return self._all_index_cache[1]
        index: dict[str, list[tuple[str, int]]] = {}
        for number in reversed(self.list_versions()):
            try:
                manifest = self._manifest(number)
            except (OSError, ValueError, KeyError):
                continue
            entries = list(manifest.files.values())
            if manifest.revision:
                rev = manifest.revision
                entries.append(FileEntry(rev["path"], rev["sha256"], rev["size"]))
            for entry in entries:
                pairs = index.setdefault(entry.sha256, [])
                if (entry.path, entry.size) not in pairs:
                    pairs.append((entry.path, entry.size))
        self._all_index_cache = (version, index)
        return index

    def not_ready(self, files: dict[str, FileEntry]) -> list[str]:
        """Files of a version that are not fully on this computer yet.

        Google Drive shows a file before its bytes have arrived, so a size that
        disagrees with the manifest means "still syncing", not "damaged".
        """
        waiting = []
        index = self._current_index()
        for path, entry in sorted(files.items()):
            if self._history_path(entry.sha256).exists():
                continue
            candidates = index.get(entry.sha256, [])
            if not any(_has_size(self._visible(p), entry.size) for p in candidates):
                if next(self._older_sources(entry.sha256), None) is None:
                    waiting.append(path)
        return waiting

    def _find_source(self, sha256: str) -> Path | None:
        for candidate in (self._history_path(sha256), self._staged_path(sha256)):
            if candidate.exists():
                return candidate
        paths = self._current_index().get(sha256, [])
        for rel in paths:
            visible = self._visible(rel)
            if visible.exists():
                return visible
        if paths:
            raise NotReadyError(
                f"Google Drive is still syncing {paths[0]}. Try again in a minute."
            )
        return None

    def _current_index(self) -> dict[str, list[str]]:
        version = self._committed_version()
        if self._index_cache and self._index_cache[0] == version:
            return self._index_cache[1]
        index: dict[str, list[str]] = {}
        if version:
            for path, entry in self._manifest(version).files.items():
                index.setdefault(entry.sha256, []).append(path)
        self._index_cache = (version, index)
        return index

    # --- versions ------------------------------------------------------

    def list_versions(self) -> list[int]:
        found = []
        for path in (self.meta / MANIFESTS_DIR).glob("v*.committed"):
            try:
                found.append(int(path.stem.lstrip("v")))
            except ValueError:
                continue
        return sorted(found)

    def _committed_version(self) -> int:
        versions = self.list_versions()
        return versions[-1] if versions else 0

    def current_version(self) -> int:
        try:
            self.recover()
        except StorageError:
            pass  # an unfinishable push is ignored here; publishing reports it
        return self._committed_version()

    def read_manifest(self, version: int) -> bytes:
        path = self._manifest_path(version)
        if not path.exists():
            raise FileNotFoundError(f"No manifest for version {version}")
        return path.read_bytes()

    def _manifest(self, version: int) -> Manifest:
        return Manifest.from_json(self.read_manifest(version))

    def pruned_versions(self) -> set[int]:
        """Versions an older Session Sync threw away (it kept only the last five).

        Nothing is pruned any more; this only reports what was lost before.
        """
        return set(self._retention().get("pruned", []))

    def pinned_versions(self) -> set[int]:
        return set(self._retention().get("pinned", []))

    def pin_version(self, version: int, pinned: bool = True) -> None:
        if version not in self.list_versions():
            raise ValueError(f"There is no version {version}.")
        if version in self.pruned_versions():
            raise ValueError(f"Version {version} is too old to keep; its files are gone.")
        retention = self._retention()
        pins = set(retention.get("pinned", []))
        (pins.add if pinned else pins.discard)(version)
        retention["pinned"] = sorted(pins)
        self._write_retention(retention)

    def publish_manifest(self, version: int, data: bytes) -> None:
        self._ensure_layout()
        self.recover(strict=True)
        committed = self._committed_version()
        if version <= committed:
            raise VersionAlreadyExistsError(
                f"Version {version} has already been published by someone else"
            )
        self._take_lock(version)
        try:
            try:
                fd = os.open(self._manifest_path(version), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError as exc:
                raise VersionAlreadyExistsError(
                    f"Version {version} has already been published by someone else"
                ) from exc
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            self._finish_commit(version)
        finally:
            self._release_lock()

    # --- the commit ----------------------------------------------------

    def _finish_commit(self, version: int) -> None:
        manifest = self._manifest(version)
        new = manifest.files
        old = self._manifest(version - 1).files if version > 1 and self._manifest_path(version - 1).exists() else {}
        self._reconcile(new, old)
        self._place_revision(manifest)
        _write_atomic(self._marker_path(version), b"")
        shutil.rmtree(self.meta / STAGING_DIR, ignore_errors=True)
        self._index_cache = None
        self._all_index_cache = None

    def _reconcile(self, target: dict[str, FileEntry], previous: dict[str, FileEntry]) -> None:
        """Make the visible files match `target`, keeping anything replaced.

        A file `target` no longer lists is left where it is: an older revision
        uses it, and Past Revisions open straight from this folder.
        Safe to run again after a crash: it only acts on files that do not
        already match, and an overwritten file is moved to history before its
        replacement goes in.
        """
        for path in sorted(target):
            dest = self._visible(path)
            want, had = target[path], previous.get(path)
            if had and want.sha256 == had.sha256 and dest.exists():
                continue
            if dest.exists():
                current = hash_file(dest)
                if current == want.sha256:
                    continue
                self._to_history(dest, current)
            self._place(want, dest, previous, siblings=target)

    def _place_revision(self, manifest: Manifest) -> None:
        """Save this push's main Live Set into Past Revisions/ (idempotent)."""
        rev = manifest.revision
        if not rev:
            return
        marker = rev.get("project_marker")
        if marker:
            # Live finds the project (and so a Past Revision's samples) by this folder
            self._visible(marker).mkdir(parents=True, exist_ok=True)
        dest = self._visible(rev["path"])
        if dest.exists() and hash_file(dest) == rev["sha256"]:
            return
        self._place(FileEntry(rev["path"], rev["sha256"], rev["size"]), dest, manifest.files)

    def _to_history(self, source: Path, sha256: str) -> None:
        hist = self._history_path(sha256)
        if hist.exists():
            source.unlink()  # the same bytes are already kept
            return
        hist.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, hist)

    def _place(
        self,
        want: FileEntry,
        dest: Path,
        previous: dict[str, FileEntry],
        *,
        siblings: dict[str, FileEntry] | None = None,
    ) -> None:
        staged = self._staged_path(want.sha256)
        if staged.exists():
            # Moved, not copied: it was verified on the way into staging, and in a
            # Google Drive folder a move is a rename, while a copy uploads it again.
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
            return
        # the same content at another path of this version, already moved into place
        for path, entry in (siblings or {}).items():
            other = self._visible(path)
            if entry.sha256 == want.sha256 and other != dest and _has_size(other, want.size):
                try:
                    _copy_verified(other, dest, want.sha256)
                    return
                except NotReadyError:
                    continue
        history = self._history_path(want.sha256)
        if history.exists():
            _copy_verified(history, dest, want.sha256)
            return
        for path, entry in previous.items():
            other = self._visible(path)
            if entry.sha256 == want.sha256 and other.exists() and other != dest:
                _copy_verified(other, dest, want.sha256)
                return
        for other in self._older_sources(want.sha256):
            if other != dest:
                try:
                    _copy_verified(other, dest, want.sha256)
                    return
                except NotReadyError:
                    continue
        raise StorageError(f"The shared copy is missing the file {want.path}.")

    # --- crash recovery ------------------------------------------------

    def _pending_version(self) -> int | None:
        committed = self._committed_version()
        pending = []
        for path in (self.meta / MANIFESTS_DIR).glob("v*.json"):
            try:
                number = int(path.stem.lstrip("v"))
            except ValueError:
                continue
            if number > committed:
                pending.append(number)
        return max(pending) if pending else None

    def recover(self, *, strict: bool = False) -> None:
        """Finish (or undo) a push whose client died part way."""
        pending = self._pending_version()
        if pending is None:
            return
        if self._lock_is_fresh():
            if strict:
                raise StorageError("Someone is pushing this song right now. Try again in a minute.")
            return
        try:
            self._finish_commit(pending)
        except StorageError:
            self._undo(pending)

    def _undo(self, version: int) -> None:
        manifest = self._manifest(version)
        new = manifest.files
        old = self._manifest(version - 1).files if version > 1 and self._manifest_path(version - 1).exists() else {}
        self._reconcile(old, new)
        if manifest.revision:  # a revision that never happened leaves no Past Revisions file
            self._visible(manifest.revision["path"]).unlink(missing_ok=True)
        self._manifest_path(version).unlink(missing_ok=True)
        shutil.rmtree(self.meta / STAGING_DIR, ignore_errors=True)

    def _lock_path(self) -> Path:
        return self.meta / STAGING_DIR / LOCK_FILE

    def _lock_is_fresh(self) -> bool:
        try:
            age = time.time() - self._lock_path().stat().st_mtime
        except FileNotFoundError:
            return False
        return age < STALE_COMMIT_SECONDS

    def _take_lock(self, version: int) -> None:
        lock = self._lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and not self._lock_is_fresh():
            lock.unlink(missing_ok=True)
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise StorageError(
                "Someone is pushing this song right now. Try again in a minute."
            ) from exc
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"version": version, "at": time.time()}))

    def _release_lock(self) -> None:
        self._lock_path().unlink(missing_ok=True)

    # --- retention (pins and what an older version pruned) -----------------

    def _retention(self) -> dict:
        path = self.meta / RETENTION_FILE
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_retention(self, retention: dict) -> None:
        _write_atomic(self.meta / RETENTION_FILE, json.dumps(retention).encode("utf-8"))

    # --- presence ------------------------------------------------------

    def write_session(self, key: str, data: dict) -> None:
        self._ensure_layout()
        _write_atomic(
            self.meta / PRESENCE_DIR / f"{key}.json",
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
        )

    def list_sessions(self) -> list[dict]:
        sessions = []
        for path in sorted((self.meta / PRESENCE_DIR).glob("*.json")):
            try:
                sessions.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sessions

    def delete_session(self, key: str) -> None:
        (self.meta / PRESENCE_DIR / f"{key}.json").unlink(missing_ok=True)


def _has_size(path: Path, size: int) -> bool:
    try:
        return path.stat().st_size == size
    except OSError:
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".incoming")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _copy_verified(source: Path, dest: Path, sha256: str) -> None:
    """Copy while hashing; the destination appears only if the bytes are right."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".incoming")
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, tmp.open("wb") as out:
            while chunk := src.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            raise NotReadyError(
                f"{source.name} is not fully there yet (Google Drive may still be syncing). "
                "Try again in a minute."
            )
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
