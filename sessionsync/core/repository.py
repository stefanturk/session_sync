"""The repository engine: pull, work, push.

There is one current version of a project and no merging. A push is allowed
only when this computer started from the version the remote is still on; the
moment someone else publishes first, everyone else must pull before they can
push. Nothing local is ever deleted outright — replaced and removed files go to
a local trash folder first.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sessionsync.adapters import get_adapter
from sessionsync.adapters.base import DAWAdapter
from sessionsync.core.errors import (
    LocalChangesWouldBeReplacedError,
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

REPOSITORY_FORMAT_VERSION = 1


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
    ) -> Repository:
        """Set up a working copy of an existing repository and download it."""
        from sessionsync.core.localstate import LocalState

        metadata = storage.read_repo_metadata()
        if metadata is None:
            raise NotAProjectError("No SessionSync project was found at that location.")

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
        repo.pull()
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
        metadata = storage.read_repo_metadata() or {}
        adapter = get_adapter(metadata.get("project_format", "ableton"))
        return cls(project_root, storage, adapter, author, state)

    # --- where things stand --------------------------------------------

    @property
    def base_version(self) -> int:
        return self.state.base_version

    def remote_version(self) -> int:
        return self.storage.current_version()

    def read_manifest(self, version: int) -> Manifest:
        return Manifest.from_json(self.storage.read_manifest(version))

    def scan(self) -> dict[str, FileEntry]:
        """Hash every tracked file in the project as it is right now."""
        return build_file_map(
            self.project_root,
            self.adapter.enumerate_tracked_files(self.project_root),
            self.hash_cache,
        )

    def base_files(self) -> dict[str, FileEntry]:
        """The project as it was at the version this copy started from."""
        if self.base_version == 0:
            return {}
        return self.read_manifest(self.base_version).files

    def review(self) -> ManifestDiff:
        """What has changed locally since this session started (or the last sync)."""
        baseline = self.state.baseline
        if baseline is not None:
            before = {
                path: FileEntry(path=path, sha256=info["sha256"], size=info["size"])
                for path, info in baseline.items()
            }
        else:
            before = self.base_files()
        return diff_file_maps(before, self.scan())

    def status(self) -> Status:
        from sessionsync.core.session import active_sessions

        remote_version = self.remote_version()
        files = self.scan()
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
        )

    def history(self) -> list[VersionSummary]:
        summaries = []
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

        session = build_session(self.author, self.base_version, new_session_key(self.author))
        self.state.session = session
        self.state.baseline = {p: e.to_dict() for p, e in self.scan().items()}
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
        Changes not put back    -> "unpushed" (stays until put back or discarded).
        Neither                 -> the note is removed.
        """
        from sessionsync.core.session import build_session, new_session_key

        diff = diff_file_maps(self.base_files(), self.scan())
        if not live_here and not diff.has_changes:
            self._end_session_marker()
            return None
        session = self.state.session or build_session(
            self.author, self.base_version, new_session_key(self.author)
        )
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

    def _base_tracks(self):
        if self.base_version == 0:
            return None
        return self.read_manifest(self.base_version).tracks

    def end_session(self) -> None:
        self._end_session_marker()
        self.state.baseline = None

    def other_sessions(self) -> list[dict]:
        from sessionsync.core.session import active_sessions

        return active_sessions(self.storage, exclude=self._session_key())

    # --- the two operations that matter --------------------------------

    def push(self, note: str | None = None) -> PushResult:
        remote_version = self.remote_version()
        if remote_version != self.base_version:
            raise StalePushError(self.base_version, remote_version)

        files = self.scan()
        diff = diff_file_maps(self.base_files(), files)
        if not diff.has_changes:
            raise NothingToPushError("Nothing has changed since your last push.")

        # Upload content first: the manifest must never name an object that is
        # not already stored, whatever happens to this process.
        wanted = {e.sha256 for e in files.values()}
        already_there = self.storage.existing_objects(wanted)
        uploaded_objects = uploaded_bytes = 0
        for entry in _one_file_per_hash(files, skip=already_there):
            self.storage.upload_object(entry.sha256, self.project_root / entry.path)
            uploaded_objects += 1
            uploaded_bytes += entry.size

        new_version = remote_version + 1
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
        )
        try:
            # Publishing the manifest is the commit point — one winner per version.
            self.storage.publish_manifest(new_version, manifest.to_json().encode("utf-8"))
        except VersionAlreadyExistsError:
            raise StalePushError(self.base_version, self.remote_version()) from None

        self.state.base_version = new_version
        self.state.baseline = None
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

    def pull(self, *, replace_my_versions: bool = False) -> PullResult:
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
        # a file missing from this Mac must come back even if nobody touched it.
        for path, entry in sorted(target.files.items()):
            local = self.project_root / path
            if local.exists():
                if self.hash_cache.hash_file(local) == entry.sha256:
                    continue  # already have exactly this content
                if path not in diff.added and path not in diff.modified:
                    continue  # locally edited, and this version does not change it
                self._to_trash(local, trash)
            self.storage.download_object(entry.sha256, local)
            downloaded += entry.size

        for path in diff.deleted:
            self._to_trash(self.project_root / path, trash)

        self.state.base_version = target_version
        self.state.baseline = None
        return PullResult(from_version, target_version, diff, downloaded)

    # --- local safety ---------------------------------------------------

    def changes_a_pull_would_replace(self, target: Manifest) -> list[str]:
        """Files edited here since the last sync that the new version also changes.

        Work that only exists on this Mac — a new recording nobody else has
        touched — is never at risk, so it is not reported.
        """
        base = self.base_files()
        local = self.scan()
        at_risk = []
        for path, incoming in target.files.items():
            local_entry = local.get(path)
            if local_entry is None or local_entry.sha256 == incoming.sha256:
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

    def _end_session_marker(self) -> None:
        session = self.state.session
        if session:
            self.storage.delete_session(session["key"])
            self.state.session = None


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


def _describe(storage: StorageProvider) -> str:
    describe = getattr(storage, "describe", None)
    return describe() if describe else str(storage)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
