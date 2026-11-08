"""The repository engine: pull, work, push.

There is one current version of a project and no merging. A push is allowed
only when this computer started from the version the remote is still on; the
moment someone else publishes first, everyone else must pull before they can
push. Nothing local is ever deleted outright — replaced files go to a local
trash folder first, and files a newer version no longer uses are left alone.

Every push first runs Consolidate (the adapter's `plan_push`): only the files
the Live Sets use are sent, files from outside the project are copied in, and
the main Set is saved into Past Revisions/.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sessionsync.adapters import get_adapter
from sessionsync.adapters.base import DAWAdapter
from sessionsync.core import osinfo
from sessionsync.core.errors import (
    ConversionFailedError,
    DriveEditsError,
    LocalChangesWouldBeReplacedError,
    NewerFormatError,
    NothingToPushError,
    NotAProjectError,
    ProjectBehindError,
    StalePushError,
)
from sessionsync.core.hashing import HashCache
from sessionsync.core.summary import describe_version, summarise
from sessionsync.core.manifest import (
    FileEntry,
    Manifest,
    ManifestDiff,
    build_file_map,
    diff_file_maps,
)
from sessionsync.storage.base import NotReadyError, StorageProvider, VersionAlreadyExistsError

REPOSITORY_FORMAT_VERSION = 2


@dataclass
class PushResult:
    version: int
    diff: ManifestDiff
    uploaded_objects: int
    uploaded_bytes: int


@dataclass
class PullResult:
    from_version: int
    to_version: int
    diff: ManifestDiff
    downloaded_bytes: int = 0


@dataclass
class Status:
    state: str
    local_version: int
    remote_version: int
    diff: ManifestDiff
    project_bytes: int
    upload_bytes: int
    already_stored_bytes: int
    active_sessions: list = field(default_factory=list)
    #: Consolidate's view: unused, outside, missing files (None for other DAWs)
    plan: object | None = None
    #: Live Sets someone saved straight into the shared copy in Drive
    drive_edits: list = field(default_factory=list)

    @property
    def versions_behind(self) -> int:
        return max(0, self.remote_version - self.local_version)

    @property
    def has_local_changes(self) -> bool:
        return self.diff.has_changes


@dataclass
class VersionSummary:
    version: int
    author: dict | None
    note: str | None
    created_at: str | None
    added: list[str]
    modified: list[str]
    deleted: list[str]
    size_change: int
    summary: str = ""
    restorable: bool = True  # False only for versions an older Session Sync pruned
    revision_path: str | None = None  # its main Live Set in Past Revisions/
    sizes: dict = field(default_factory=dict)  # size of each added or changed file


class Repository:
    def __init__(
        self,
        project_root: Path,
        storage: StorageProvider,
        adapter: DAWAdapter,
        author: dict,
        state,
    ):
        self.project_root = Path(project_root)
        self.storage = storage
        self.adapter = adapter
        self.author = author
        self.state = state
        self.hash_cache = HashCache(state.db_path)
        self._known_cache: tuple[int, dict[str, str]] | None = None
        self.last_plan = None
        self._restart_after = 0  # sharing again after the history was lost: carry on numbering
        self._looking = False  # a status check (may skip files Live holds open), not a push
        self.in_use: list[str] = []  # files the last look could not read

    # --- setting up ----------------------------------------------------

    @classmethod
    def create(
        cls,
        project_root: Path,
        storage: StorageProvider,
        *,
        adapter: DAWAdapter,
        author: dict,
        project_name: str | None = None,
    ) -> Repository:
        """Turn an existing project folder into a synced repository."""
        from sessionsync.core.localstate import LocalState

        project_root = Path(project_root)
        if not adapter.detect_project(project_root):
            raise NotAProjectError(
                f"{project_root.name} does not look like an Ableton project folder. "
                "Choose the folder that contains the Live Set (.als)."
            )

        metadata = {
            "repository_format_version": REPOSITORY_FORMAT_VERSION,
            "repository_id": str(uuid.uuid4()),
            "project_name": project_name or project_root.name,
            "project_format": adapter.format_name,
            "project_adapter_version": adapter.adapter_version,
            "created_by": author,
            "created_at": _now(),
        }
        storage.write_repo_metadata(metadata)

        state = LocalState.create(
            project_root,
            repository_id=metadata["repository_id"],
            remote=_describe(storage),
        )
        return cls(project_root, storage, adapter, author, state)

    @classmethod
    def clone(
        cls,
        storage: StorageProvider,
        project_root: Path,
        *,
        author: dict,
        progress=None,
    ) -> Repository:
        """Set up a working copy of an existing repository and download it."""
        from sessionsync.core.localstate import LocalState

        metadata = storage.read_repo_metadata()
        if metadata is None:
            raise NotAProjectError(_why_no_song(storage))

        project_root = Path(project_root)
        project_root.mkdir(parents=True, exist_ok=True)
        state = LocalState.create(
            project_root,
            repository_id=metadata["repository_id"],
            remote=_describe(storage),
        )
        repo = cls(
            project_root,
            storage,
            get_adapter(metadata["project_format"]),
            author,
            state,
        )
        repo.pull(progress=progress)
        return repo

    @classmethod
    def open(
        cls,
        project_root: Path,
        *,
        author: dict,
        storage: StorageProvider | None = None,
    ) -> Repository:
        from sessionsync.core.localstate import LocalState
        from sessionsync.storage import storage_from_location

        state = LocalState.open(project_root)
        storage = storage or storage_from_location(state.remote)
        metadata = storage.read_repo_metadata()
        if metadata is None:
            root = getattr(storage, "root", None)
            if root is not None and Path(root).is_dir() and state.base_version:
                from sessionsync.core.errors import SharedHistoryMissingError

                raise SharedHistoryMissingError(Path(root))
            metadata = {}
        adapter = get_adapter(metadata.get("project_format", "ableton"))
        return cls(project_root, storage, adapter, author, state)

    @classmethod
    def reshare(cls, project_root: Path, *, author: dict) -> "Repository":
        """The shared copy lost its hidden history: give it back from this copy.

        Every computer keeps the manifest of the version it is on, for exactly
        this. Putting it back makes the files still in the song folder count
        again, so only what is missing is uploaded, from this copy. Changes
        made here stay for an ordinary Push. Without that manifest (a copy last
        synced by an older Session Sync), this copy is shared as the next version.
        Collaborators carry on as usual: same song, same numbering.
        """
        from sessionsync.core.localstate import LocalState
        from sessionsync.storage import storage_from_location

        project_root = Path(project_root)
        state = LocalState.open(project_root)
        storage = storage_from_location(state.remote)
        if storage.read_repo_metadata() is not None:
            raise ValueError("The shared copy still has its history; nothing to repair.")
        adapter = get_adapter("ableton")
        metadata = {
            "repository_format_version": REPOSITORY_FORMAT_VERSION,
            "repository_id": state.repository_id or str(uuid.uuid4()),
            "project_name": project_root.name,
            "project_format": adapter.format_name,
            "project_adapter_version": adapter.adapter_version,
            "created_by": author,
            "created_at": _now(),
            "reshared_from": {"author": author, "version": state.base_version, "at": _now()},
        }
        song_live = state._get("song_live_major")
        if song_live:
            metadata["live_major"] = song_live
        storage.write_repo_metadata(metadata)
        repo = cls(project_root, storage, adapter, author, state)
        put_back = repo._put_back_base()
        adopt = getattr(storage, "adopt_sets", None)
        if adopt is not None:
            adopt()  # what is in the song folder now was shared, not saved there by hand
        if not put_back:
            repo._restart_after = state.base_version
            state.base_version = 0
            try:
                repo.push(note="Shared again from this copy")
            except NothingToPushError:
                pass
        return repo

    def _remember_base(self) -> None:
        """Keep the manifest of the version this copy is on (see `reshare`)."""
        try:
            if self.base_version:
                self.state._set("base_manifest", self.storage.read_manifest(self.base_version).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - only a repair aid
            osinfo.log(f"could not keep the base manifest: {exc!r}")

    def _put_back_base(self) -> bool:
        """Republish the kept manifest of this copy's version. False if it cannot be."""
        kept = self.state._get("base_manifest")
        if not kept:
            return False
        manifest = Manifest.from_json(kept)
        if manifest.version != self.base_version or not manifest.files:
            return False
        shows = getattr(self.storage, "shows", lambda entry: False)
        present = self.storage.existing_objects({e.sha256 for e in manifest.files.values()})
        for entry in manifest.files.values():
            if entry.sha256 in present or shows(entry):
                continue
            local = self.project_root / entry.path
            if not local.is_file() or self._hash(local) != entry.sha256:
                # neither Drive nor this copy has it byte for byte as it was
                # (changed here, or kept in this computer's Live version)
                return False
            self.storage.upload_object(entry.sha256, local)
            present.add(entry.sha256)
        if manifest.revision and not shows(
            FileEntry(manifest.revision["path"], manifest.revision["sha256"], manifest.revision["size"])
        ) and manifest.revision["sha256"] not in present:
            manifest.revision = None
        self.storage.publish_manifest(manifest.version, manifest.to_json().encode("utf-8"))
        return True

    # --- where things stand --------------------------------------------

    @property
    def base_version(self) -> int:
        return self.state.base_version

    def remote_version(self) -> int:
        return self.storage.current_version()

    def read_manifest(self, version: int) -> Manifest:
        return Manifest.from_json(self.storage.read_manifest(version))

    def scan(self) -> dict[str, FileEntry]:
        """What a push would send right now, keyed by its path in the shared copy.

        For Ableton that is Consolidate's selection: the files the Sets use
        (including ones copied in from outside), not every file in the folder.
        """
        plan = self.plan_push(rewrite=False)
        if plan is None:
            return build_file_map(
                self.project_root,
                self.adapter.enumerate_tracked_files(self.project_root),
                self.hash_cache,
            )
        return self._as_shared(self._file_map(plan.files))

    def plan_push(self, *, rewrite: bool = False):
        """Run Consolidate. None when the adapter has no such step."""
        plan_push = getattr(self.adapter, "plan_push", None)
        if plan_push is None:
            return None
        plan = plan_push(
            self.project_root,
            known=self._known_paths(),
            hash_file=self._hash,
            rewrite=rewrite,
        )
        self.last_plan = plan
        return plan

    def _file_map(self, sources: dict[str, Path]) -> dict[str, FileEntry]:
        return {
            rel: FileEntry(path=rel, sha256=self._hash(src), size=src.stat().st_size)
            for rel, src in sources.items()
        }

    def _hash(self, path: Path) -> str:
        """A file's hash. On Windows, Live keeps a loaded song's audio open and
        nobody else may read it: a status check then goes by what is already
        known (it was checked, or copied here verified, at that size); a push
        says to close the song in Live."""
        from sessionsync.core.errors import FileInUseError

        path = Path(path)
        try:
            return self.hash_cache.hash_file(path)
        except PermissionError:
            if not self._looking:
                raise FileInUseError(path) from None
        rel = path.relative_to(self.project_root).as_posix() if path.is_relative_to(self.project_root) else ""
        self.in_use.append(rel or path.name)
        known = self.hash_cache.last_known(path)
        if known is None and rel:
            base = self.base_files().get(rel)
            size = path.stat().st_size
            if base is not None and base.size == size:
                known = base.source_sha256 or base.sha256
            elif base is not None and base.sha256 in self.state.twins:
                known = self.state.twins[base.sha256]
        # Unknown and unreadable (Live recording into it): shown as changed, checked later.
        return known or f"in-use:{path.stat().st_size}:{path.stat().st_mtime_ns}"

    def _look(self, work):
        """Run a read-only look at the project, tolerating files Live holds open."""
        self._looking, self.in_use = True, []
        try:
            return work()
        finally:
            self._looking = False

    def _as_shared(self, files: dict[str, FileEntry]) -> dict[str, FileEntry]:
        """A Set this computer pushed (and Consolidate rewrote) counts as unchanged."""
        base = self.base_files()
        twins = self.state.twins
        for path, entry in list(files.items()):
            pushed = base.get(path)
            if pushed is None:
                continue
            if pushed.source_sha256 == entry.sha256 or twins.get(pushed.sha256) == entry.sha256:
                files[path] = pushed
        return files

    def _matches(self, entry: FileEntry, sha256: str) -> bool:
        """This computer's file is that version of it (as is, or in this computer's form)."""
        return entry.matches(sha256) or self.state.twins.get(entry.sha256) == sha256

    def _known_paths(self) -> dict[str, str]:
        """{path: hash} for every file any version has held, newest wins."""
        try:
            versions = self.storage.list_versions()
        except Exception:
            return {}
        latest = versions[-1] if versions else 0
        if self._known_cache and self._known_cache[0] == latest:
            return self._known_cache[1]
        known: dict[str, str] = {}
        for version in versions:
            try:
                manifest = self.read_manifest(version)
            except (FileNotFoundError, ValueError, KeyError):
                continue
            known.update({p: e.sha256 for p, e in manifest.files.items()})
        self._known_cache = (latest, known)
        return known

    def base_files(self) -> dict[str, FileEntry]:
        """The project as it was at the version this copy started from."""
        if self.base_version == 0:
            return {}
        try:
            return self.read_manifest(self.base_version).files
        except FileNotFoundError:
            # the shared history was lost and shared again from another copy
            kept = self.state._get("base_manifest")
            if kept:
                manifest = Manifest.from_json(kept)
                if manifest.version == self.base_version:
                    return manifest.files
            raise

    def review(self) -> ManifestDiff:
        """What has changed here since the version this copy is on.

        Always against that version, never against "when you pressed start":
        work done before the app was looking is still work to push.
        """
        return diff_file_maps(self.base_files(), self._look(self.scan))

    def status(self) -> Status:
        from sessionsync.core.session import active_sessions

        remote_version = self.remote_version()
        files = self._look(self.scan)
        diff = diff_file_maps(self.base_files(), files)

        project_bytes = sum(e.size for e in files.values())
        stored = self.storage.existing_objects({e.sha256 for e in files.values()})
        upload_bytes = sum(e.size for e in files.values() if e.sha256 not in stored)

        behind = remote_version > self.base_version
        if behind and diff.has_changes:
            state = "push_blocked"
        elif behind:
            state = "behind"
        elif diff.has_changes:
            state = "local_changes"
        else:
            state = "up_to_date"

        return Status(
            state=state,
            local_version=self.base_version,
            remote_version=remote_version,
            diff=diff,
            project_bytes=project_bytes,
            upload_bytes=upload_bytes,
            already_stored_bytes=project_bytes - upload_bytes,
            active_sessions=active_sessions(self.storage, exclude=self._session_key()),
            plan=self.last_plan,
            drive_edits=self.drive_edits(),
        )

    def history(self) -> list[VersionSummary]:
        summaries = []
        pruned = getattr(self.storage, "pruned_versions", lambda: set())()
        previous: dict[str, FileEntry] = {}
        previous_bytes = 0
        previous_manifest: Manifest | None = None
        for version in self.storage.list_versions():
            manifest = self.read_manifest(version)
            diff = diff_file_maps(previous, manifest.files)
            summaries.append(
                VersionSummary(
                    version=version,
                    author=manifest.author,
                    note=manifest.note,
                    created_at=manifest.created_at,
                    added=diff.added,
                    modified=diff.modified,
                    deleted=diff.deleted,
                    size_change=manifest.total_bytes - previous_bytes,
                    summary=describe_version(previous_manifest, manifest),
                    restorable=version not in pruned,
                    revision_path=(manifest.revision or {}).get("path"),
                    sizes=diff.sizes,
                )
            )
            previous, previous_bytes = manifest.files, manifest.total_bytes
            previous_manifest = manifest
        return list(reversed(summaries))

    def versions_after(self, version: int) -> list[VersionSummary]:
        """Versions newer than `version`, oldest first: "what happened while I was away"."""
        newer = [h for h in self.history() if h.version > version]
        return sorted(newer, key=lambda h: h.version)

    def restore_copy(self, version: int, destination: Path) -> Path:
        """Build an old version into a brand-new folder, leaving this project alone.

        The point is going back when someone breaks a song: open the copy in Live,
        compare, take what you want. Refuses a destination that already exists, so
        it can never overwrite a project.
        """
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(f"{destination} already exists; choose a new folder.")
        if version not in self.storage.list_versions():
            raise ValueError(f"There is no version {version}.")
        if version in getattr(self.storage, "pruned_versions", lambda: set())():
            raise ValueError(f"Version {version} is older than the versions that are kept.")
        manifest = self.read_manifest(version)
        destination.mkdir(parents=True)
        try:
            for path, entry in sorted(manifest.files.items()):
                self.storage.download_object(entry.sha256, destination / path)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return destination

    def _track_fingerprints(self) -> dict[str, str] | None:
        fingerprint = getattr(self.adapter, "track_fingerprints", None)
        if fingerprint is None:
            return None
        try:
            return fingerprint(self.project_root)
        except Exception:  # a summary must never be the reason a push fails
            return None

    # --- sessions -------------------------------------------------------

    def start_session(self, *, force: bool = False) -> dict:
        """Begin a working session: check you are current, then tell the others."""
        from sessionsync.core.session import build_session, new_session_key

        remote_version = self.remote_version()
        if remote_version > self.base_version and not force:
            raise ProjectBehindError(self.base_version, remote_version)

        self._end_session_marker()  # an automatic note of ours gives way to the real session
        session = build_session(self.author, self.base_version, new_session_key(self.author))
        self.state.session = session
        self.storage.write_session(session["key"], session)
        return session

    def heartbeat(self, state: str | None = None) -> None:
        """Tell the repository this session is still going."""
        session = self.state.session
        if not session:
            return
        session["last_heartbeat"] = _now()
        if state:
            session["state"] = state
        self.state.session = session
        self.storage.write_session(session["key"], session)

    def refresh_presence(self, live_here: bool) -> str | None:
        """Keep the shared copy's "who is doing what" note true. Returns the state.

        Live open on this song  -> "working".
        Changes not pushed      -> "unpushed" (stays until pushed or discarded).
        Neither                 -> the note is removed.
        """
        from sessionsync.core.session import build_session, new_session_key

        existing = self.state.session
        if existing and not existing.get("auto") and live_here:
            self.heartbeat()  # a session started by hand (CLI) keeps its own note while Live is open
            return existing.get("state")
        diff = diff_file_maps(self.base_files(), self.scan())
        if not live_here and not diff.has_changes:
            self._end_session_marker()
            return None
        session = existing or build_session(
            self.author, self.base_version, new_session_key(self.author)
        )
        session["auto"] = True
        previous = session.get("state")
        session["state"] = "working" if live_here else "unpushed"
        session["last_heartbeat"] = _now()
        session["base_version"] = self.base_version
        if diff.has_changes:
            session["changed"] = summarise(
                diff, self._base_tracks(), self._track_fingerprints()
            ) or None
            if previous != "unpushed" or not session.get("unpushed_since"):
                session["unpushed_since"] = _now()
        else:
            session.pop("changed", None)
            session.pop("unpushed_since", None)
        self.state.session = session
        self.storage.write_session(session["key"], session)
        return session["state"]

    def set_summary(self, diff: ManifestDiff) -> str:
        """What changed inside the main Live Set, track by track ("Changed Main, Drums")."""
        touched = [p for p in diff.modified + diff.added if p.lower().endswith(".als")]
        if not touched:
            return ""
        text = summarise(diff_file_maps({}, {}), self._base_tracks(), self._track_fingerprints())
        return "" if text == "No changes" else text

    def changes_fingerprint(self, diff: ManifestDiff) -> str:
        """Short id of a set of unpushed changes, to notice ones made while the app was closed."""
        text = "|".join(sorted(diff.added) + ["~"] + sorted(diff.modified) + ["-"] + sorted(diff.deleted))
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def _base_tracks(self):
        if self.base_version == 0:
            return None
        return self.read_manifest(self.base_version).tracks

    def end_session(self) -> None:
        self._end_session_marker()

    def other_sessions(self) -> list[dict]:
        from sessionsync.core.session import active_sessions

        return active_sessions(self.storage, exclude=self._session_key())

    # --- the two operations that matter --------------------------------

    def push(self, note: str | None = None) -> PushResult:
        self.check_format()
        remote_version = self.remote_version()
        if remote_version != self.base_version:
            raise StalePushError(self.base_version, remote_version)
        edits = self.drive_edits()
        if edits:
            raise DriveEditsError(edits)
        migrate = getattr(self.storage, "migrate", None)
        if migrate is not None:
            migrate()  # an older shared copy gets the layout Live cannot open by mistake

        # Consolidate first: what the Sets use, outside files copied in, and the
        # Sets re-pointed where needed. Any reason it is unsafe stops the push here,
        # before anything is uploaded.
        plan = self.plan_push(rewrite=True)
        scratch = Path(tempfile.mkdtemp(prefix="sessionsync-push-"))
        try:
            if plan is not None:
                plan.check()
                files, sources = self._consolidated_files(plan, scratch)
            else:
                files = self.scan()
                sources = {e.sha256: self.project_root / e.path for e in files.values()}
            files, sources, merged = self._in_song_version(files, sources, scratch)
            result = self._publish(files, sources, plan, note, remote_version)
            self._after_push_versions(files, merged)
            return result
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # --- wrong pushes: a second look before, and undo after -----------------

    def push_warnings(self, diff: ManifestDiff | None = None, merges=()) -> list[str]:
        """Reasons this push looks like a mistake, in plain words. Empty for an
        ordinary push; otherwise the Push dialog asks once more before it goes.

        `merges`: [(new set, song's set)] that Push folds back together, so a
        "save as new" copy is not mistaken for a second set.
        """
        if self.base_version == 0:
            return []
        base_manifest = self.read_manifest(self.base_version)
        base = base_manifest.files
        if not base:
            return []
        diff = diff if diff is not None else self.review()
        warnings = []
        main = (base_manifest.revision or {}).get("set")
        if main and main in diff.deleted:
            warnings.append(f"It removes the song's Live Set, “{main}”.")
        elif main and main in diff.modified:
            before, after = base[main].size, diff.sizes.get(main, 0)
            if before > 50_000 and after < before / 2:
                warnings.append(
                    f"The song's Live Set, “{main}”, is less than half its size "
                    f"({after // 1024} KB, was {before // 1024} KB)."
                )
        gone = len(diff.deleted)
        gone_bytes = sum(base[p].size for p in diff.deleted)
        all_bytes = sum(e.size for e in base.values()) or 1
        if gone >= 3 and (gone > len(base) * 0.2 or gone_bytes > all_bytes * 0.2):
            warnings.append(
                f"It removes {gone} of the song's {len(base)} files "
                f"({gone_bytes * 100 // all_bytes}% of it)."
            )
        merged = {new for new, _ in merges}
        had_top_set = any(p.lower().endswith(".als") and "/" not in p for p in base)
        extra = [
            p for p in diff.added
            if p.lower().endswith(".als") and "/" not in p and p not in merged
        ]
        if had_top_set and extra:
            warnings.append(
                "It adds another Live Set beside the song's own: "
                + ", ".join(f"“{p}”" for p in extra)
                + ". Fine if you meant to; if Live saved a copy, keep working in one set."
            )
        return warnings

    def make_latest(self, version: int, note: str | None = None) -> PushResult:
        """Undo: share a new version holding exactly the files of `version`.

        Nothing is lost. The versions after it stay in History, so this can be
        undone the same way. Nobody's own copy changes until they pull.
        """
        self.check_format()
        versions = self.storage.list_versions()
        if version not in versions:
            raise ValueError(f"There is no version {version}.")
        if version in getattr(self.storage, "pruned_versions", lambda: set())():
            raise ValueError(f"Version {version} is older than the versions that are kept.")
        remote = self.remote_version()
        old = self.read_manifest(version)
        latest = self.read_manifest(remote)
        diff = diff_file_maps(latest.files, old.files)
        if not diff.has_changes:
            raise NothingToPushError(f"The latest version already holds what v{version} held.")
        wanted = {e.sha256 for e in old.files.values()}
        if wanted - set(self.storage.existing_objects(wanted)):
            raise ValueError(f"Some of the files of v{version} are no longer in the shared folder.")

        new_version = remote + 1
        note = note or f"Back to v{version}"
        revision = None
        if old.revision and old.revision.get("set") in old.files:
            revision = dict(old.revision)
            revision["path"] = past_revision_name(
                new_version, self.author, note, Path(old.revision["set"]).stem
            )
        manifest = Manifest(
            version=new_version,
            repository_id=self.state.repository_id,
            parent_version=remote,
            project_format=self.adapter.format_name,
            author=self.author,
            note=note,
            files=dict(old.files),
            summary=f"Back to v{version} · " + summarise(diff, latest.tracks, old.tracks),
            tracks=old.tracks,
            revision=revision,
        )
        try:
            self.storage.publish_manifest(new_version, manifest.to_json().encode("utf-8"))
        except VersionAlreadyExistsError:
            raise StalePushError(self.base_version, self.remote_version()) from None
        return PushResult(new_version, diff, 0, 0)

    # --- Live versions ----------------------------------------------------

    def song_live_major(self) -> int | None:
        """The Live version the shared copy is kept in (the oldest the song's sets use)."""
        meta = self.storage.read_repo_metadata() or {}
        if meta.get("live_major"):
            return int(meta["live_major"])
        cached = self.state._get("song_live_major")
        if cached:
            return int(cached)
        from sessionsync.adapters.ableton import versions

        majors = []
        sets = {
            rel: e for rel, e in self.base_files().items()
            if rel.lower().endswith(".als") and "/" not in rel
        }
        if sets:
            scratch = Path(tempfile.mkdtemp(prefix="sessionsync-version-"))
            try:
                for n, entry in enumerate(sets.values()):
                    target = scratch / f"{n}.als"
                    try:
                        self.storage.download_object(entry.sha256, target)
                    except Exception:
                        continue
                    major = versions.set_major(target)
                    if major:
                        majors.append(major)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        else:
            majors = [m for m in (versions.set_major(p) for p in self.project_root.glob("*.als")) if m]
        if not majors:
            return None
        song = min(majors)
        self.state._set("song_live_major", song)
        return song

    def saved_as_new(self, files: dict[str, FileEntry] | None = None) -> list[tuple[str, str]]:
        """Sets a newer Live saved beside the song's set ("My Song 12.als" next to
        "My Song.als", when Live asked to save as a new set): [(new set, song's set)].
        Pushing makes the new one the song's set again, in the song's version."""
        from sessionsync.adapters.ableton import versions

        song = self.song_live_major()
        if song is None:
            return []
        files = files if files is not None else self._look(self.scan)
        base = self.base_files()
        pairs = []
        for rel in sorted(files):
            if not rel.lower().endswith(".als") or "/" in rel:
                continue
            shared = base.get(rel)
            if shared is not None and files[rel].sha256 == shared.sha256:
                major = self._shared_major(shared)  # as shared, whatever form it has here
            else:
                major = versions.set_major(self.project_root / rel)
            if not major or major <= song:
                continue
            stem = Path(rel).stem
            for other in sorted(files):
                if other == rel or "/" in other or not other.lower().endswith(".als"):
                    continue
                other_stem = Path(other).stem
                if not stem.startswith(other_stem + " ") or other not in base:
                    continue
                if files[other].sha256 != base[other].sha256:
                    continue  # the song's set was changed here too: nothing is merged over it
                pairs.append((rel, other))
                break
        return pairs

    def _shared_major(self, entry: FileEntry) -> int | None:
        """The Live version of a set as it is kept in the shared copy."""
        from sessionsync.adapters.ableton import versions

        known = self.state._get("set_majors", {}) or {}
        if entry.sha256 in known:
            return known[entry.sha256]
        scratch = Path(tempfile.mkdtemp(prefix="sessionsync-version-"))
        try:
            target = scratch / "set.als"
            self.storage.download_object(entry.sha256, target)
            major = versions.set_major(target)
        except Exception:
            major = None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        known[entry.sha256] = major
        self.state._set("set_majors", known)
        return major

    def lost_in_push(self) -> dict[str, list[str]]:
        """{set: [what the song's Live version cannot hold]} for sets this push converts."""
        from sessionsync.adapters.ableton import versions

        song = self.song_live_major()
        out: dict[str, list[str]] = {}
        if song is None:
            return out
        base = self.base_files()
        for rel, entry in self._look(self.scan).items():
            if not rel.lower().endswith(".als") or "/" in rel or base.get(rel) == entry:
                continue
            path = self.project_root / rel
            major = versions.set_major(path)
            if major and major > song:
                try:
                    lost = versions.convert(path.read_bytes(), song).lost
                except versions.ConversionError:
                    continue
                if lost:
                    out[rel] = lost
        return out

    def _in_song_version(self, files, sources, scratch: Path):
        """Every set this push uploads, turned into the song's Live version.
        A set Live saved as new beside the song's set takes that set's place."""
        from sessionsync.adapters.ableton import versions

        song = self.song_live_major()
        if song is None:
            return files, sources, []
        merged = self.saved_as_new(files)
        for new, old in merged:
            entry = files.pop(new)
            files[old] = FileEntry(path=old, sha256=entry.sha256, size=entry.size,
                                   source_sha256=entry.source_sha256)
        for n, (rel, entry) in enumerate(sorted(files.items())):
            if not rel.lower().endswith(".als") or entry.sha256 not in sources:
                continue
            data = Path(sources[entry.sha256]).read_bytes()
            major = versions.live_major(data)
            if not major or major == song:
                continue
            try:
                converted = versions.convert(data, song)
            except versions.ConversionError as exc:
                raise ConversionFailedError(rel, song, str(exc)) from None
            temp = scratch / f"v{n}.als"
            temp.write_bytes(converted.data)
            sha = hashlib.sha256(converted.data).hexdigest()
            files[rel] = FileEntry(path=rel, sha256=sha, size=len(converted.data),
                                   source_sha256=entry.source_sha256 or entry.sha256)
            sources[sha] = temp
        return self._as_shared(files), sources, merged

    def _after_push_versions(self, files, merged) -> None:
        """Keep the song's version in repo.json, and tidy a merged "saved as new" set."""
        from sessionsync.adapters.ableton import versions

        song = self.song_live_major()
        meta = self.storage.read_repo_metadata() or {}
        if song and not meta.get("live_major"):
            meta["live_major"] = song
            try:
                self.storage.write_repo_metadata(meta)
            except Exception:
                pass
        if not merged:
            return
        trash = self._new_trash_folder()
        mine = versions.my_live_major()
        for new, old in merged:
            new_path, old_path = self.project_root / new, self.project_root / old
            entry = files[old]
            if old_path.exists():
                self._to_trash(old_path, trash)
            if not new_path.exists():
                continue
            if mine is None or mine >= (versions.set_major(new_path) or 0):
                new_path.rename(old_path)  # the newer Live keeps working in the song's own set
                self.state.add_twin(entry.sha256, self.hash_cache.hash_file(old_path))
            else:
                # This computer's Live cannot open the newer set: it gets the song's version.
                self._to_trash(new_path, trash)
                self.storage.download_object(entry.sha256, old_path)
                self.hash_cache.remember(old_path, entry.sha256)
                self._for_this_computer(old_path, entry)
        self._known_cache = None

    def _for_this_computer(self, local: Path, entry: FileEntry) -> None:
        """A set just pulled: in this computer's Live version, with its samples
        found on this computer. The shared version stays the one recorded."""
        from sessionsync.adapters.ableton import versions

        if not versions.LOCALIZE or not local.name.lower().endswith(".als"):
            return
        data = local.read_bytes()
        out = data
        mine, theirs = versions.my_live_major(), versions.live_major(data)
        # Only down: a newer Live opens an older set itself (and its "save as new"
        # copy is merged back on Push). Making Live 12 sets ourselves is not tried yet.
        if mine and theirs and mine < theirs:
            try:
                out = versions.convert(data, mine).data
            except versions.ConversionError as exc:
                osinfo.log(f"could not make {local.name} a Live {mine} set: {exc}")
        try:
            out = versions.localize(out, local, self.project_root)
        except versions.ConversionError:
            pass
        if out == data:
            return
        temp = local.with_name(local.name + ".converting")
        temp.write_bytes(out)
        temp.replace(local)
        sha = hashlib.sha256(out).hexdigest()
        self.hash_cache.remember(local, sha)
        self.state.add_twin(entry.sha256, sha)

    def _consolidated_files(self, plan, scratch: Path) -> tuple[dict[str, FileEntry], dict[str, Path]]:
        files = self._file_map(plan.files)
        sources = {files[rel].sha256: src for rel, src in plan.files.items()}
        for n, (rel, data) in enumerate(sorted(plan.rewritten.items())):
            sha = hashlib.sha256(data).hexdigest()
            temp = scratch / f"{n}.als"
            temp.write_bytes(data)
            local = files[rel]
            files[rel] = FileEntry(path=rel, sha256=sha, size=len(data), source_sha256=local.sha256)
            sources[sha] = temp
        return self._as_shared(files), sources

    def _publish(self, files, sources, plan, note, remote_version) -> PushResult:
        diff = diff_file_maps(self.base_files(), files)
        if not diff.has_changes:
            raise NothingToPushError("Nothing has changed since your last push.")

        # Upload content first: the manifest must never name an object that is
        # not already stored, whatever happens to this process.
        wanted = {e.sha256 for e in files.values()}
        already_there = self.storage.existing_objects(wanted)
        uploaded_objects = uploaded_bytes = 0
        for entry in _one_file_per_hash(files, skip=already_there):
            self.storage.upload_object(entry.sha256, sources[entry.sha256])
            uploaded_objects += 1
            uploaded_bytes += entry.size

        new_version = (remote_version or self._restart_after) + 1
        revision = None
        main = plan.main_set if plan is not None else None
        if main and main in files:
            entry = files[main]
            revision = {
                "path": past_revision_name(new_version, self.author, note, Path(main).stem),
                "sha256": entry.sha256,
                "size": entry.size,
                "set": main,
                "project_marker": getattr(self.adapter, "project_marker", None),
            }
        previous_tracks = (
            self.read_manifest(remote_version).tracks if remote_version else None
        )
        new_tracks = self._track_fingerprints()
        summary = summarise(
            diff, previous_tracks, new_tracks, first=remote_version == 0
        )
        manifest = Manifest(
            version=new_version,
            repository_id=self.state.repository_id,
            parent_version=remote_version or None,
            project_format=self.adapter.format_name,
            author=self.author,
            note=note,
            files=files,
            summary=summary,
            tracks=new_tracks,
            revision=revision,
        )
        try:
            # Publishing the manifest is the commit point — one winner per version.
            self.storage.publish_manifest(new_version, manifest.to_json().encode("utf-8"))
        except VersionAlreadyExistsError:
            raise StalePushError(self.base_version, self.remote_version()) from None

        self.state.base_version = new_version
        self._remember_base()
        self.state.last_push = {
            "version": new_version,
            "at": _now(),
            "note": note,
            "added": len(diff.added),
            "modified": len(diff.modified),
            "deleted": len(diff.deleted),
            "uploaded_bytes": uploaded_bytes,
        }
        self._end_session_marker()
        return PushResult(new_version, diff, uploaded_objects, uploaded_bytes)

    def pull(self, *, replace_my_versions: bool = False, progress=None) -> PullResult:
        """Bring this copy up to the newest version.

        `progress(Progress)` is called before each file is fetched and once at
        the end, so a window can say how far along it is.
        """
        self.check_format()
        target_version = self.remote_version()
        from_version = self.base_version
        if target_version == 0 or target_version == from_version:
            return PullResult(from_version, from_version, diff_file_maps({}, {}))

        target = self.read_manifest(target_version)
        diff = diff_file_maps(self.base_files(), target.files)

        if not replace_my_versions:
            at_risk = self.changes_a_pull_would_replace(target)
            if at_risk:
                raise LocalChangesWouldBeReplacedError(at_risk)

        # A shared copy that is still arriving (Google Drive) must not be half-copied.
        waiting = getattr(self.storage, "not_ready", None)
        if waiting is not None:
            unready = waiting(target.files)
            if unready:
                shown = ", ".join(unready[:2]) + (" and more" if len(unready) > 2 else "")
                raise NotReadyError(
                    f"Google Drive is still syncing {len(unready)} file(s) ({shown}). "
                    "Try again in a minute."
                )

        trash = self._new_trash_folder()
        downloaded = 0
        # Walk the whole target version, not just what changed between versions:
        # a file missing from this computer must come back even if nobody touched it.
        wanted = []
        for path, entry in sorted(target.files.items()):
            local = self.project_root / path
            if local.exists():
                if self._matches(entry, self._hash(local)):
                    continue  # already have exactly this content
                if path not in diff.added and path not in diff.modified:
                    continue  # locally edited, and this version does not change it
            wanted.append((path, entry))
        total_bytes = sum(entry.size for _, entry in wanted)
        for number, (path, entry) in enumerate(wanted, 1):
            local = self.project_root / path
            if progress is not None:
                progress(Progress(number, len(wanted), downloaded, total_bytes, path, entry.size))
            if local.exists():
                self._to_trash(local, trash)
            try:
                self.storage.download_object(entry.sha256, local)
            except NotReadyError:
                edits = self.drive_edits()
                if edits:
                    raise DriveEditsError(edits) from None
                raise
            self.hash_cache.remember(local, entry.sha256)  # copied and verified: never read again
            self._for_this_computer(local, entry)
            downloaded += entry.size

        # A set the new version dropped (merged back into the song's set) goes to
        # the trash if it is exactly what was shared, so it is not pushed again.
        base_before = self.base_files()
        for path in diff.deleted:
            local = self.project_root / path
            entry = base_before.get(path)
            if path.lower().endswith(".als") and entry is not None and local.exists() \
                    and self._matches(entry, self._hash(local)):
                self._to_trash(local, trash)

        # Files the new version no longer uses stay on this computer: an older
        # revision may need them, and they were never anyone else's to remove.
        if progress is not None and wanted:
            progress(Progress(len(wanted), len(wanted), downloaded, total_bytes, "", 0))
        downloaded += self._fetch_past_revisions()

        self.state.base_version = target_version
        self._remember_base()
        return PullResult(from_version, target_version, diff, downloaded)

    def _fetch_past_revisions(self) -> int:
        """Bring down every Past Revisions Live Set this computer lacks (they are small)."""
        fetched = 0
        marker = getattr(self.adapter, "project_marker", None)
        if marker:
            (self.project_root / marker).mkdir(parents=True, exist_ok=True)
        for version in self.storage.list_versions():
            try:
                rev = self.read_manifest(version).revision
            except (FileNotFoundError, ValueError, KeyError):
                continue
            if not rev:
                continue
            local = self.project_root / rev["path"]
            if local.exists():
                continue
            try:
                self.storage.download_object(rev["sha256"], local)
                fetched += rev["size"]
            except (FileNotFoundError, NotReadyError):
                continue  # still arriving; the next pull picks it up
        return fetched

    # --- going back -----------------------------------------------------

    def open_revision(self, version: int) -> Path:
        """Get an old revision ready to open in Live. Returns the Live Set to open.

        Usually that is Past Revisions/Rev N ….als inside this project: anything
        the revision uses that this computer lacks is fetched first, and nothing
        here is overwritten. If a file the revision needs has different content
        here now, the revision is built as a separate copy beside the project.
        """
        if version not in self.storage.list_versions():
            raise ValueError(f"There is no version {version}.")
        manifest = self.read_manifest(version)
        rev = manifest.revision
        needed = {
            path: entry
            for path, entry in manifest.files.items()
            if not path.lower().endswith(".als")
        }
        missing, conflicts = [], []
        for path, entry in sorted(needed.items()):
            local = self.project_root / path
            if not local.exists():
                missing.append(entry)
            elif not self._matches(entry, self._hash(local)):
                if not path.startswith("Ableton Project Info/"):
                    conflicts.append(path)

        if rev and not conflicts:
            for entry in missing:
                self.storage.download_object(entry.sha256, self.project_root / entry.path)
            target = self.project_root / rev["path"]
            if not target.exists():
                self.storage.download_object(rev["sha256"], target)
            return target

        side = _free_name(self.project_root.parent / f"{self.project_root.name} (Rev {version})")
        self.restore_copy(version, side)
        main = (rev or {}).get("set")
        if main and (side / main).exists():
            return side / main
        sets = sorted(side.glob("*.als"))
        return sets[0] if sets else side

    # --- being behind ---------------------------------------------------

    def catch_up(self, aside_parent: Path | None = None) -> "CatchUp":
        """Someone pushed first. Keep my version, bring theirs, say who did what.

        1. My whole project is copied aside (a clone on APFS, so it costs little).
        2. Their latest version replaces mine in the project; anything replaced also
           goes to the local trash.
        3. My Live Set is kept next to theirs as "<name> (mine).als", so both can be
           opened and tracks dragged across.
        Nothing is pushed: that stays a deliberate step.
        """
        target = self.remote_version()
        if target == self.base_version:
            raise ValueError("You already have the latest version.")
        mine_tracks = self._track_fingerprints() or {}
        base_tracks = self._base_tracks() or {}
        their_tracks = self.read_manifest(target).tracks or {}
        info = self.adapter.inspect_project(self.project_root)
        my_set = info.primary_set

        parent = Path(aside_parent) if aside_parent else self.project_root.parent
        stamp = datetime.now().strftime("%Y-%m-%d %H%M")
        aside = _free_name(parent / f"{self.project_root.name} (mine before v{target}, {stamp})")
        _clone_tree(self.project_root, aside, skip={".sessionsync"})

        saved_set = None
        if my_set is not None:
            saved_set = my_set.read_bytes()
        pulled = self.pull(replace_my_versions=True)

        beside = None
        if saved_set is not None and my_set is not None:
            beside = _free_name(my_set.with_name(f"{my_set.stem} (mine).als"))
            beside.write_bytes(saved_set)

        return CatchUp(
            aside=aside,
            my_set=beside,
            pulled=pulled,
            theirs=_track_changes(base_tracks, their_tracks),
            mine=_track_changes(base_tracks, mine_tracks),
        )

    # --- the shared copy itself ----------------------------------------

    def check_format(self) -> None:
        """Refuse to touch a shared copy laid out by a newer Session Sync."""
        metadata = self.storage.read_repo_metadata() or {}
        found = int(metadata.get("repository_format_version", 1) or 1)
        if found > REPOSITORY_FORMAT_VERSION:
            raise NewerFormatError(found, REPOSITORY_FORMAT_VERSION)

    def drive_edits(self) -> list[str]:
        """Live Sets someone saved straight into the shared copy (relative to it)."""
        finder = getattr(self.storage, "drive_edits", None)
        if finder is None:
            return []
        try:
            return finder()
        except OSError:
            return []  # Drive half-synced: look again next time

    def rescue_drive_edits(self) -> list["Rescued"]:
        """Bring sets saved straight into Drive into this copy, and put Drive right.

        The saved set lands where it can be pushed, and nothing here is
        overwritten unless it is exactly the version Drive already has:

        - this copy is current and its set is unchanged -> the rescued set
          replaces it (the old one goes to the trash folder; Drive has it too);
        - otherwise -> it is put beside the set as "<Song> (edited in Drive …).als"
          for you to open and compare.
        """
        self.check_format()
        rescue = getattr(self.storage, "rescue_drive_edits", None)
        if rescue is None:
            return []
        current = self.read_manifest(self.base_version).files if self.base_version else {}
        local_sets = {
            rel: self.project_root / rel for rel in current if rel.lower().endswith(".als")
        }
        moved = rescue(local_copies=local_sets)
        behind = self.remote_version() != self.base_version
        trash = None
        out = []
        for source in moved:
            rel = _rescued_relative(source)
            entry = current.get(rel)
            target = self.project_root / rel
            unchanged = (
                entry is not None
                and target.exists()
                and self._matches(entry, self._hash(target))
            )
            if unchanged and not behind:
                trash = trash or self._new_trash_folder()
                self._to_trash(target, trash)
                shutil.copy2(source, target)
                out.append(Rescued(target, replaced=True))
                continue
            when = osinfo.strftime(datetime.fromtimestamp(source.stat().st_mtime), "%-d %b %H.%M")
            side = _free_name(self.project_root / f"{Path(rel).stem} (edited in Drive, {when}).als")
            shutil.copy2(source, side)
            out.append(Rescued(side, replaced=False))
        return out

    def open_target(self) -> Path:
        """The Live Set to open: the main set of this copy."""
        info = self.adapter.inspect_project(self.project_root)
        primary = getattr(info, "primary_set", None)
        if primary is None:
            raise NotAProjectError(f"There is no Live Set in {self.project_root.name}.")
        return Path(primary)

    # --- local safety ---------------------------------------------------

    def changes_a_pull_would_replace(self, target: Manifest) -> list[str]:
        """Files edited here since the last sync that the new version also changes.

        Work that only exists on this computer — a new recording nobody else has
        touched — is never at risk, so it is not reported.
        """
        base = self.base_files()
        local = self.scan()
        at_risk = []
        for path, incoming in target.files.items():
            local_entry = local.get(path)
            if local_entry is None or self._matches(incoming, local_entry.sha256):
                continue
            base_entry = base.get(path)
            if base_entry is not None and local_entry.sha256 != base_entry.sha256:
                at_risk.append(path)
        return sorted(at_risk)

    def _new_trash_folder(self) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        return self.state.trash_dir / stamp

    def _to_trash(self, path: Path, trash: Path) -> None:
        """Never delete in place — keep a copy the user can recover."""
        if not path.exists():
            return
        dest = trash / path.relative_to(self.project_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))

    def _session_key(self) -> str:
        session = self.state.session
        return session["key"] if session else ""

    def stop_tracking(self) -> Path:
        """This computer stops syncing the song; nothing is deleted.

        The folder stays an ordinary Live project, and the shared copy in Drive
        is not touched. This copy's presence note goes, so nobody sees it
        "working". Session Sync's own bookkeeping is moved aside, not deleted
        (rename it back to `.sessionsync` to undo). Returns where it went.
        """
        try:
            self._end_session_marker()
        except Exception as exc:  # noqa: BLE001 - Drive may be away; the note expires anyway
            osinfo.log(f"could not remove the presence note: {exc!r}")
        self.state.close()
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        aside = self.project_root / f".sessionsync-removed {stamp}"
        shutil.move(str(self.state.dir), str(aside))
        return aside

    def _end_session_marker(self) -> None:
        session = self.state.session
        if session:
            self.storage.delete_session(session["key"])
            self.state.session = None


@dataclass
class Rescued:
    path: Path      # where the rescued set now is, in this copy
    replaced: bool  # True: it became the song's set; False: it sits beside it


def _rescued_relative(source: Path) -> str:
    """'…/.sessionsync/rescued/<stamp>/Sub/Song.als' -> 'Sub/Song.als'."""
    parts = source.parts
    i = parts.index("rescued")
    return Path(*parts[i + 2:]).as_posix()


@dataclass
class CatchUp:
    aside: Path
    my_set: Path | None
    pulled: "PullResult"
    theirs: list[str]  # "Added Sax", "Changed Drums" ...
    mine: list[str]

    @property
    def overlap(self) -> list[str]:
        """Tracks both of you changed: the ones that need a human decision."""
        names = lambda items: {i.split(" ", 1)[1] for i in items}
        return sorted(names(self.theirs) & names(self.mine))


def _track_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changes = []
    for name in sorted(set(after) - set(before)):
        changes.append(f"Added {name}")
    for name in sorted(set(before) & set(after)):
        if before[name] != after[name]:
            changes.append(f"Changed {name}")
    for name in sorted(set(before) - set(after)):
        changes.append(f"Removed {name}")
    return changes


def _free_name(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} {n}{path.suffix}")
        n += 1
    return candidate


def _clone_tree(source: Path, dest: Path, *, skip: set[str]) -> None:
    """Copy a folder, using filesystem clones where the Mac offers them."""
    import subprocess
    import sys

    dest.mkdir(parents=True)
    try:
        for entry in sorted(source.iterdir()):
            if entry.name in skip:
                continue
            target = dest / entry.name
            if sys.platform == "darwin":
                result = subprocess.run(["cp", "-cR", str(entry), str(target)], capture_output=True)
                if result.returncode == 0:
                    continue
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def _one_file_per_hash(files: dict[str, FileEntry], *, skip: set[str]) -> list[FileEntry]:
    """Duplicate content inside a project only needs uploading once."""
    seen: set[str] = set(skip)
    unique = []
    for path in sorted(files):
        entry = files[path]
        if entry.sha256 in seen:
            continue
        seen.add(entry.sha256)
        unique.append(entry)
    return unique


def past_revision_name(version: int, author: dict | None, note: str | None, song: str) -> str:
    """"Past Revisions/Rev 0007 - 2026-11-06 - Stefan - new chorus.als"."""
    who = (author or {}).get("name") or ""
    parts = [f"Rev {version:04d}", datetime.now().strftime("%Y-%m-%d")]
    if who:
        parts.append(_filename_safe(who, 30))
    if note and note.strip():
        parts.append(_filename_safe(note, 40))
    elif song:
        parts.append(_filename_safe(song, 40))
    return f"Past Revisions/{' - '.join(p for p in parts if p)}.als"


def _filename_safe(text: str, limit: int) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:limit].rstrip(" .")


def _describe(storage: StorageProvider) -> str:
    describe = getattr(storage, "describe", None)
    return describe() if describe else str(storage)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _why_no_song(storage) -> str:
    """Why a shared song cannot be read, said so the person can do something about it."""
    root = getattr(storage, "root", None)
    if root is None:
        return "No SessionSync project was found at that location."
    root = Path(root)
    meta = root / ".sessionsync"
    if not root.is_dir():
        return (
            f"Session Sync cannot reach {root}. Is Google Drive running and signed in? "
            "Nothing was changed."
        )
    if not meta.is_dir():
        return (
            f"Google Drive has not brought this song's Session Sync files to this computer "
            f"yet (there is no .sessionsync folder in {root}). Wait until Google Drive says "
            "it is up to date, then try again. Nothing was changed."
        )
    inside = sorted(p.name for p in meta.iterdir() if p.name != "Icon\r")
    if "state.db" in inside:
        return (
            f"{meta} has a state.db in it and no repo.json: Google Drive on this computer is "
            "holding on to a leftover from an older Session Sync instead of the song's real "
            "files. Quit Google Drive and start it again so it fetches the song afresh. "
            "Nothing was changed."
        )
    return (
        f"The song's Session Sync files have not fully arrived from Google Drive yet "
        f"(no repo.json in {meta}; found: {', '.join(inside) or 'nothing'}). Wait until "
        "Google Drive says it is up to date, then try again. Nothing was changed."
    )


@dataclass(frozen=True)
class Progress:
    """How far a pull is: file `number` of `files` (`path`, `size` bytes) is next."""

    number: int
    files: int
    done_bytes: int
    total_bytes: int
    path: str
    size: int

    def describe(self) -> str:
        gb = 1024 ** 3
        amount = f"{self.done_bytes / gb:.1f} of {self.total_bytes / gb:.1f} GB"
        if not self.path:
            return f"All {self.files} files copied ({amount})."
        name = Path(self.path).name
        return f"File {self.number} of {self.files} ({amount})\n{name} — {_mb(self.size)}"


def _mb(size: int) -> str:
    return f"{size / 1024 ** 2:.0f} MB" if size >= 1024 ** 2 else f"{max(1, size // 1024)} KB"
