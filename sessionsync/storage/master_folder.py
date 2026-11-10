"""The shared copy: a song folder in Drive that nobody opens by mistake.

    <Song>/
      ▶ Open in Live.webloc                    opens YOUR copy (see core/launcher.py)
      Read me.txt
      Song data — managed by Session Sync/     the project files
        <Song>.als-shared                      Live Sets, renamed so Live will not open them
        Samples/…
      .sessionsync/                            manifests, history, presence, staging

Opening and saving the shared copy in Live is how one person's edit silently
missed everyone else (it happened: the app watched the working copy while Live
saved into Drive). So the shared files sit one folder down and their Live Sets
carry an extension Live does not open. Manifests keep the real names; only
`_visible` knows about the disguise.

Format 1 copies (project files at the top, plain `.als`) are still read, and
`migrate` turns them into format 2 under the commit lock.

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

from sessionsync.core import launcher, locations
from sessionsync.core.errors import CloudRootError
from sessionsync.core.hashing import hash_file
from sessionsync.core.manifest import FileEntry, Manifest
from sessionsync.core.winfiles import open_for_reading
from sessionsync.storage.base import NotReadyError, StorageError, VersionAlreadyExistsError

META_DIR = ".sessionsync"
REPO_FILE = "repo.json"
RETENTION_FILE = "retention.json"
MANIFESTS_DIR = "manifests"
HISTORY_DIR = "history"
PRESENCE_DIR = "presence"
STAGING_DIR = "staging"
LOCK_FILE = "commit.lock"
RESCUED_DIR = "rescued"
TRASH_DIR = "trash"
MIGRATING_FILE = "migrating"
ADOPTED_FILE = "adopted_sets.json"  # sets already here when the history was shared again

#: Where the project files live inside the song folder (format 2).
DATA_DIR = "Song data — managed by Session Sync"
#: What a Live Set is called in the shared copy, so double-clicking it does nothing harmful.
SHARED_SET_SUFFIX = ".als-shared"
#: The layout this code writes. 1 = project files at the top of the song folder.
LAYOUT = 2

# A push that has been silent this long has certainly died.
STALE_COMMIT_SECONDS = 10 * 60
_CHUNK = 1024 * 1024


class MasterFolderStorage:
    PREFIX = "master:"

    def __init__(self, root: Path):
        self.root = Path(root)
        self._index_cache: tuple[int, dict[str, list[str]]] | None = None
        self._all_index_cache: tuple[int, dict[str, list[tuple[str, int]]]] | None = None
        self._layout_cache: tuple[float, int] | None = None
        self._edit_hashes: dict[str, tuple[float, int, str]] = {}

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

    def layout(self) -> int:
        """1 or 2. A folder with no metadata yet is new, so it gets the current layout."""
        path = self.meta / REPO_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return LAYOUT
        if self._layout_cache and self._layout_cache[0] == mtime:
            return self._layout_cache[1]
        try:
            value = int(json.loads(path.read_text(encoding="utf-8")).get("repository_format_version", 1))
        except (OSError, ValueError, TypeError):
            value = 1
        self._layout_cache = (mtime, value)
        return value

    @property
    def data_root(self) -> Path:
        """Where the project files are: one folder down, or (format 1) the top."""
        return self.root / DATA_DIR if self.layout() >= 2 else self.root

    def _visible(self, rel_path: str) -> Path:
        base = self.data_root
        target = (base / rel_path).resolve()
        if base.resolve() not in target.parents:
            raise StorageError(f"Refusing a path outside the project: {rel_path}")
        return base / disguise(rel_path) if base != self.root else base / rel_path

    def _ensure_layout(self) -> None:
        if locations.is_cloud_root(self.root):
            raise CloudRootError(self.root)
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

    @classmethod
    def song_around(cls, folder: Path) -> Path | None:
        """The shared song `folder` is, or is somewhere inside. None if neither.

        Looks upwards: a folder whose `.sessionsync` names a repository, or the
        song holding a "Song data — managed by Session Sync" folder (which says
        so even when the `.sessionsync` beside it has gone missing).
        """
        folder = Path(folder)
        for candidate in (folder, *folder.parents):
            if candidate.name == DATA_DIR:
                return candidate.parent
            meta = candidate / META_DIR
            if (meta / REPO_FILE).exists() and not (meta / "state.db").exists():
                return candidate
            if (candidate / DATA_DIR).is_dir() and (
                meta.is_dir() or any((candidate / name).exists() for name in launcher.FRONT_DOOR)
            ):
                return candidate
        return None

    @classmethod
    def holds_songs(cls, folder: Path) -> bool:
        """`folder` has shared songs inside it (it is a shared folder, not a song)."""
        try:
            children = [p for p in Path(folder).iterdir() if p.is_dir() and not p.name.startswith(".")]
        except OSError:
            return False
        return any(cls.song_around(child) == child for child in children)

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
        self._layout_cache = None
        if self.layout() >= 2:
            self.ensure_front_door()

    def ensure_front_door(self) -> None:
        """The launcher and the read-me at the top of the song folder (format 2 only)."""
        metadata = self.read_repo_metadata() or {}
        if self.layout() < 2 or not metadata.get("repository_id"):
            return
        launcher.write_front_door(self.root, metadata["repository_id"], self.root.name)

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

    def shows(self, entry: FileEntry) -> bool:
        """The file is in the song folder at its own path, at its size (the
        hidden history being gone, say): it need not be uploaded again."""
        return _has_size(self._visible(entry.path), entry.size)

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
            if (self.meta / MIGRATING_FILE).exists() and not self._lock_is_fresh():
                self.migrate()  # a computer died part way through moving the files
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

    # --- format 1 -> 2 ---------------------------------------------------

    def needs_migration(self) -> bool:
        return self.layout() < 2 and self.read_repo_metadata() is not None

    def migrate(self) -> list[Path]:
        """Move a format-1 shared copy into the format-2 layout. Returns rescued sets.

        Every step is a rename inside the song folder, so Google Drive uploads
        nothing again. It runs under the commit lock and can be run again after
        a crash at any point; the format number is written last.

        1. A Live Set whose bytes differ from the current version was saved in
           Drive directly: it is moved to .sessionsync/rescued/, never lost.
        2. Every file any version used moves into the data folder.
        3. Anything else at the top (Live's Backup folder, crash files) goes to
           .sessionsync/trash/.
        """
        if not self.needs_migration():
            if (self.meta / MIGRATING_FILE).exists():  # cut short after the format was written
                self.ensure_front_door()
                (self.meta / MIGRATING_FILE).unlink(missing_ok=True)
            return []
        self._ensure_layout()
        version = self._committed_version()
        self._take_lock(version + 1)
        try:
            _write_atomic(self.meta / MIGRATING_FILE, b"")
            rescued = self._rescue_from(self.root, legacy=True)
            data = self.root / DATA_DIR
            known = {rel for pairs in self._all_index().values() for rel, _ in pairs}
            for version_ in self.list_versions():
                try:
                    rev = self._manifest(version_).revision or {}
                except (OSError, ValueError, KeyError):
                    continue
                if rev.get("project_marker"):
                    known.add(rev["project_marker"])
            for rel in sorted(known):
                source = self.root / rel
                dest = data / disguise(rel)
                if source.is_file() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, dest)
                elif source.is_dir():
                    (data / rel).mkdir(parents=True, exist_ok=True)
            trash = self.meta / TRASH_DIR / _stamp()
            for item in sorted(self.root.iterdir()):
                if item.name in (META_DIR, DATA_DIR) or launcher.is_front_door(item.name):
                    continue
                if item.is_dir() and not any(p.is_file() for p in item.rglob("*")):
                    shutil.rmtree(item, ignore_errors=True)  # emptied by the moves above
                    continue
                trash.mkdir(parents=True, exist_ok=True)
                os.replace(item, trash / item.name)
            metadata = self.read_repo_metadata() or {}
            metadata["repository_format_version"] = LAYOUT
            _write_atomic(
                self.meta / REPO_FILE,
                json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
            )
            self._layout_cache = None
            self._index_cache = self._all_index_cache = None
            self.ensure_front_door()
            (self.meta / MIGRATING_FILE).unlink(missing_ok=True)
            return rescued
        finally:
            self._release_lock()

    # --- edits made straight in Drive -------------------------------------

    def drive_edits(self) -> list[str]:
        """Live Sets in the shared copy that nobody pushed: saved in Drive directly.

        Paths relative to the song folder. Cheap: only Live Sets are looked at,
        and each is hashed again only when its size or time changes.
        """
        version = self._committed_version()
        if not version:
            return []
        expected = self._expected_sets(version)
        found = []
        data = self.data_root
        if not data.is_dir():
            return []
        legacy = data == self.root
        for path in sorted(data.rglob("*")):
            parts = path.relative_to(self.root).parts
            if not path.is_file() or META_DIR in parts or (legacy and parts[0] == DATA_DIR):
                continue
            name = path.name.lower()
            if not (name.endswith(".als") or name.endswith(SHARED_SET_SUFFIX)):
                continue
            rel = undisguise(path.relative_to(data).as_posix())
            if _in_backup(rel):
                continue  # Live's own safety copies: tidied away by rescue, not news
            want = expected.get(rel)
            if want is None:
                found.append(path.relative_to(self.root).as_posix())
            elif self._quick_hash(path) not in want:
                found.append(path.relative_to(self.root).as_posix())
        return found

    def adopt_sets(self) -> None:
        """After the history was shared again: the Live Sets already in the song
        folder (Past Revisions of the lost versions, mostly) are nobody's edit."""
        version = self._committed_version()
        expected = self._expected_sets(version) if version else {}
        adopted = {}
        data = self.data_root
        for path in sorted(data.rglob("*")) if data.is_dir() else ():
            parts = path.relative_to(self.root).parts
            name = path.name.lower()
            if not path.is_file() or META_DIR in parts:
                continue
            if not (name.endswith(".als") or name.endswith(SHARED_SET_SUFFIX)):
                continue
            rel = undisguise(path.relative_to(data).as_posix())
            sha = self._quick_hash(path)
            if sha not in expected.get(rel, set()):
                adopted.setdefault(rel, []).append(sha)
        _write_atomic(self.meta / ADOPTED_FILE, json.dumps(adopted, indent=1).encode("utf-8"))

    def _expected_sets(self, version: int) -> dict[str, set[str]]:
        """{set path: acceptable hashes}. Past Revisions are fixed; current sets are current."""
        expected: dict[str, set[str]] = {}
        try:
            adopted = json.loads((self.meta / ADOPTED_FILE).read_bytes())
        except (OSError, ValueError):
            adopted = {}
        for rel, hashes in adopted.items():
            if rel.lower().endswith(".als"):
                expected.setdefault(rel, set()).update(hashes)
        for number in self.list_versions():
            try:
                manifest = self._manifest(number)
            except (OSError, ValueError, KeyError):
                continue
            if manifest.revision:
                expected.setdefault(manifest.revision["path"], set()).add(manifest.revision["sha256"])
            if number == version:
                for rel, entry in manifest.files.items():
                    if rel.lower().endswith(".als"):
                        expected[rel] = {entry.sha256}
        for rel in list(expected):
            if not rel.lower().endswith(".als"):
                expected.pop(rel)
        # A set an earlier version had, that the current one dropped, still sits in place.
        older: dict[str, set[str]] = {}
        for sha, pairs in self._all_index().items():
            for rel, _ in pairs:
                if rel.lower().endswith(".als") and rel not in expected:
                    older.setdefault(rel, set()).add(sha)
        expected.update(older)
        return expected

    def _quick_hash(self, path: Path) -> str:
        stat = path.stat()
        key = str(path)
        cached = self._edit_hashes.get(key)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        digest = hash_file(path)
        self._edit_hashes[key] = (stat.st_mtime, stat.st_size, digest)
        return digest

    def rescue_drive_edits(self, *, local_copies: dict[str, Path] | None = None) -> list[Path]:
        """Move sets saved straight into Drive out of the way, and put the real ones back.

        Returns where the rescued sets now are (inside .sessionsync/rescued/), so
        the caller can bring them into the person's own copy. `local_copies`
        ({set path: file on this computer}) supplies the real set when Drive's
        only copy was the one that got overwritten. A second computer running
        this a moment later finds nothing left to do.
        """
        version = self._committed_version()
        self._take_lock(version + 1)
        try:
            rescued = self._rescue_from(self.data_root, legacy=self.layout() < 2)
            self._restore_sets(version, local_copies or {})
            backup = self.data_root / "Backup"
            if backup.is_dir():
                trash = self.meta / TRASH_DIR / _stamp()
                trash.mkdir(parents=True, exist_ok=True)
                os.replace(backup, trash / "Backup")
            self._edit_hashes.clear()
            return rescued
        finally:
            self._release_lock()

    def _rescue_from(self, base: Path, *, legacy: bool) -> list[Path]:
        edits = self.drive_edits()
        if not edits:
            return []
        dest_root = self.meta / RESCUED_DIR / _stamp()
        moved = []
        for rel in edits:
            source = self.root / rel
            if not source.exists():
                continue
            # keep the real name, so it opens in Live once it is on someone's computer
            inner = undisguise(source.relative_to(base).as_posix()) if base in source.parents else source.name
            dest = dest_root / inner
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, dest)
            moved.append(dest)
        return moved

    def _restore_sets(self, version: int, local_copies: dict[str, Path]) -> None:
        if not version:
            return
        manifest = self._manifest(version)
        for rel, entry in manifest.files.items():
            if not rel.lower().endswith(".als"):
                continue
            dest = self._visible(rel)
            if dest.exists() and self._quick_hash(dest) == entry.sha256:
                continue
            local = local_copies.get(rel)
            if local is not None and local.exists() and hash_file(local) == entry.sha256:
                _copy_verified(local, dest, entry.sha256)
                continue
            try:
                self._place(entry, dest, {})
            except StorageError:
                pass  # nobody has it right now; the next push from anyone puts it back

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


def disguise(rel_path: str) -> str:
    """The shared copy's name for a project file: Live Sets get the shared suffix."""
    return rel_path + "-shared" if rel_path.lower().endswith(".als") else rel_path


def undisguise(rel_path: str) -> str:
    if rel_path.lower().endswith(SHARED_SET_SUFFIX):
        return rel_path[: -len("-shared")]
    return rel_path


def _in_backup(rel_path: str) -> bool:
    return any(part == "Backup" for part in Path(rel_path).parts[:-1])


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H.%M.%S")


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
        with open_for_reading(source) as src, tmp.open("wb") as out:
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
