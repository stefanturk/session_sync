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
            hash_file=self.hash_cache.hash_file,
            rewrite=rewrite,
        )
        self.last_plan = plan
        return plan

    def _file_map(self, sources: dict[str, Path]) -> dict[str, FileEntry]:
        return {
            rel: FileEntry(path=rel, sha256=self.hash_cache.hash_file(src), size=src.stat().st_size)
            for rel, src in sources.items()
        }

    def _as_shared(self, files: dict[str, FileEntry]) -> dict[str, FileEntry]:
        """A Set this computer pushed (and Consolidate rewrote) counts as unchanged."""
        base = self.base_files()
        for path, entry in list(files.items()):
            pushed = base.get(path)
            if pushed is not None and pushed.source_sha256 == entry.sha256:
                files[path] = pushed
        return files

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
        return self.read_manifest(self.base_version).files

    def review(self) -> ManifestDiff:
        """What has changed here since the version this copy is on.

        Always against that version, never against "when you pressed start":
        work done before the app was looking is still work to push.
        """
        return diff_file_maps(self.base_files(), self.scan())

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
            return self._publish(files, sources, plan, note, remote_version)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

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

        new_version = remote_version + 1
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
        for path, entry in sorted(target.files.items()):
            local = self.project_root / path
            if local.exists():
                if entry.matches(self.hash_cache.hash_file(local)):
                    continue  # already have exactly this content
                if path not in diff.added and path not in diff.modified:
                    continue  # locally edited, and this version does not change it
                self._to_trash(local, trash)
            try:
                self.storage.download_object(entry.sha256, local)
            except NotReadyError:
                edits = self.drive_edits()
                if edits:
                    raise DriveEditsError(edits) from None
                raise
            downloaded += entry.size

        # Files the new version no longer uses stay on this computer: an older
        # revision may need them, and they were never anyone else's to remove.
        downloaded += self._fetch_past_revisions()

        self.state.base_version = target_version
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
            elif not entry.matches(self.hash_cache.hash_file(local)):
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
                and entry.matches(self.hash_cache.hash_file(target))
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
            if local_entry is None or incoming.matches(local_entry.sha256):
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
