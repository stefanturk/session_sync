"""The engine, on its own thread.

Two reasons this is not called directly from the window:

1. Scanning a 6 GB project takes real time even with the hash cache, and a
   frozen window during a push is how people conclude an app has crashed.
2. `Repository` owns SQLite connections, which belong to the thread that made
   them. So the repository is created *on* this thread and never leaves it —
   which also serialises everything, so a refresh can never run inside a push.

The window talks to it only through queued signals and slots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from sessionsync.auth.identity import load_identity
from sessionsync.core import livewatch
from sessionsync.core.repository import Repository, Status


@dataclass
class Snapshot:
    """Everything the window needs to draw itself, captured off the UI thread."""

    project_root: Path
    project_name: str
    status: Status
    session_open: bool
    last_push: dict | None = None
    media_outside: list[str] = field(default_factory=list)   # copied in on Push
    media_missing: list[str] = field(default_factory=list)   # used, but nowhere on this computer
    unused: list[str] = field(default_factory=list)          # audio the Sets do not use: stays here
    unused_bytes: int = 0
    packs: list[str] = field(default_factory=list)           # Ableton Packs a collaborator needs
    problems: list[str] = field(default_factory=list)        # reasons Push would stop
    live_open: bool = False        # Live has YOUR copy of this song open
    live_on_drive: bool = False    # Live has the shared copy in Drive open (the mistake)
    set_summary: str = ""          # "Changed Main, Drums" — inside the main Live Set
    #: said once: changes made while the app was not looking ("…that's fine")
    late_note: str = ""


class Engine(QObject):
    snapshot_ready = Signal(object)  # Snapshot
    operation_done = Signal(str, object)  # name, result
    operation_failed = Signal(str, object)  # name, exception
    busy_changed = Signal(str)  # human description, or "" when idle
    checking = Signal(bool)  # a background refresh, which must not lock the window

    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = Path(project_root)
        self._repo: Repository | None = None
        self._first_snapshot = True

    # --- slots the window invokes (queued onto this thread) --------------

    @Slot()
    def refresh(self) -> None:
        """A scan, not an operation: it must never grey the window out."""
        self.checking.emit(True)
        try:
            self._emit_snapshot()
        except Exception as exc:  # noqa: BLE001 - every failure is shown to the user
            self.operation_failed.emit("refresh", exc)
        finally:
            self.checking.emit(False)

    @Slot()
    def start_session(self) -> None:
        self._run("start_session", "Starting…", lambda: self._repository().start_session())

    @Slot()
    def end_session(self) -> None:
        self._run("end_session", "Ending…", lambda: self._repository().end_session())

    @Slot()
    def close_session(self) -> None:
        """Give up the heartbeat on the way out.

        Deliberately not `end_session`: no scan, no snapshot, no dialogs — this
        runs while a tab is closing. It also asks the repository whether a
        session is open rather than trusting the window, whose snapshot may be
        minutes old; guessing wrong leaves exactly the ghost session this is
        here to prevent.
        """
        repo = self._repo
        if repo is None:
            return
        try:
            # Closing a tab ends nothing: the note stays true — "working" while Live
            # has the song open, "unpushed changes" while there are some, else gone.
            repo.refresh_presence(live_here=self._live_here(repo))
        except Exception:  # noqa: BLE001 - nothing useful to do while closing
            pass

    @Slot()
    def heartbeat(self) -> None:
        repo = self._repo
        if repo is None:
            return
        try:
            repo.refresh_presence(self._live_here(repo))
        except Exception:  # noqa: BLE001 - a missed heartbeat is not worth a dialog
            pass

    @Slot()
    def open_live(self) -> None:
        """Open your copy's main Live Set (the window does the actual opening)."""

        def work():
            repo = self._repository()
            target = repo.open_target()
            repo.refresh_presence(live_here=True)  # you are working now: tell the others
            return target

        self._run("open_live", "Opening in Live…", work)

    @Slot()
    def rescue(self) -> None:
        self._run(
            "rescue",
            "Rescuing the edit made in Drive…",
            lambda: self._repository().rescue_drive_edits(),
        )

    @Slot(str)
    def push(self, note: str) -> None:
        def do_push():
            repo = self._repository()
            repo.heartbeat(state="pushing")
            try:
                return repo.push(note=note or None)
            except Exception:
                repo.heartbeat(state="working")
                raise

        self._run("push", "Pushing…", do_push)

    @Slot(bool)
    def pull(self, replace_my_versions: bool) -> None:
        self._run(
            "pull",
            "Pulling…",
            lambda: self._repository().pull(replace_my_versions=replace_my_versions),
        )

    @Slot()
    def catch_up(self) -> None:
        self._run("catch_up", "Saving your version and catching up…", lambda: self._repository().catch_up())

    @Slot()
    def review(self) -> None:
        self._run("review", "Comparing…", lambda: self._repository().review())

    @Slot()
    def history(self) -> None:
        self._run("history", "Reading history…", lambda: self._repository().history())

    @Slot(int)
    def open_revision(self, version: int) -> None:
        self._run(
            "open_revision",
            f"Getting Rev {version} ready…",
            lambda: self._repository().open_revision(version),
        )

    @Slot(int, str)
    def restore(self, version: int, destination: str) -> None:
        self._run(
            "restore",
            f"Rebuilding v{version}…",
            lambda: self._repository().restore_copy(version, Path(destination)),
        )

    # --- internals -------------------------------------------------------

    def _run(self, name: str, busy: str, work) -> None:
        self.busy_changed.emit(busy)
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - every failure is shown to the user
            self.operation_failed.emit(name, exc)
        else:
            self.operation_done.emit(name, result)
        finally:
            # Anything that touches the project changes what the window says.
            try:
                self._emit_snapshot()
            except Exception:  # noqa: BLE001 - the failure above is the one to report
                pass
            self.busy_changed.emit("")

    @staticmethod
    def _live_here(repo: Repository) -> bool:
        try:
            return livewatch.is_open_in(repo.project_root, livewatch.open_document())
        except Exception:  # noqa: BLE001 - best effort only
            return False

    def _live_flags(self, repo: Repository) -> tuple[bool, bool]:
        """(Live has my copy open, Live has the shared Drive copy open)."""
        try:
            document = livewatch.open_document()
        except Exception:  # noqa: BLE001
            return False, False
        shared = getattr(repo.storage, "root", None)
        mine = livewatch.is_open_in(repo.project_root, document)
        drive = shared is not None and livewatch.is_open_in(Path(shared), document)
        return mine, drive

    def _late_note(self, repo: Repository, status: Status) -> str:
        """Changes made while the app was closed: say so once, and say it's fine."""
        first, self._first_snapshot = self._first_snapshot, False
        diff = status.diff
        seen = repo.state.seen_changes
        if not diff.has_changes:
            if seen is not None:
                repo.state.seen_changes = None
            return ""
        mark = repo.changes_fingerprint(diff)
        if seen != mark:
            repo.state.seen_changes = mark
        if not first or seen == mark or status.versions_behind:
            return ""
        count = len(diff.added) + len(diff.modified) + len(diff.deleted)
        files = "file" if count == 1 else "files"
        return (
            f"You changed {count} {files} since v{status.local_version} while Session Sync "
            "was closed — that's fine, nobody pushed in the meantime."
        )

    def _emit_snapshot(self) -> None:
        repo = self._repository()
        status = repo.status()
        plan = status.plan  # Consolidate: what Push would send, and what it leaves here
        live_open, live_on_drive = self._live_flags(repo)
        self.snapshot_ready.emit(
            Snapshot(
                project_root=repo.project_root,
                project_name=repo.project_root.name,
                status=status,
                session_open=bool(repo.state.session),
                last_push=repo.state.last_push,
                live_open=live_open,
                live_on_drive=live_on_drive,
                set_summary=repo.set_summary(status.diff) if status.diff.has_changes else "",
                late_note=self._late_note(repo, status),
                media_outside=[str(p) for p in plan.imported.values()] if plan else [],
                media_missing=list(plan.missing) if plan else [],
                unused=list(plan.unused) if plan else [],
                unused_bytes=plan.unused_bytes if plan else 0,
                packs=list(plan.packs) if plan else [],
                problems=list(plan.problems) if plan else [],
            )
        )

    def _repository(self) -> Repository:
        if self._repo is None:
            self._repo = Repository.open(self.project_root, author=load_identity())
        return self._repo
