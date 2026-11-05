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
    media_outside: list[str] = field(default_factory=list)
    media_missing: list[str] = field(default_factory=list)


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
            if repo.state.session and not repo.state.session.get("auto"):
                repo.end_session()
            # Leaves "unpushed changes" for the others to see, or clears the note.
            repo.refresh_presence(live_here=False)
        except Exception:  # noqa: BLE001 - nothing useful to do while closing
            pass

    @Slot()
    def heartbeat(self) -> None:
        repo = self._repo
        if repo is None:
            return
        try:
            live_here = livewatch.is_open_in(repo.project_root, livewatch.open_document())
            repo.refresh_presence(live_here)
        except Exception:  # noqa: BLE001 - a missed heartbeat is not worth a dialog
            pass

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

    def _emit_snapshot(self) -> None:
        repo = self._repository()
        outside: list[str] = []
        missing: list[str] = []
        referenced = getattr(repo.adapter, "referenced_media", None)
        if referenced is not None:
            try:
                report = referenced(repo.project_root)
                outside, missing = report.outside, report.missing
            except Exception:  # noqa: BLE001 - an unreadable set must not block status
                pass

        self.snapshot_ready.emit(
            Snapshot(
                project_root=repo.project_root,
                project_name=repo.project_root.name,
                status=repo.status(),
                session_open=bool(repo.state.session),
                last_push=repo.state.last_push,
                media_outside=outside,
                media_missing=missing,
            )
        )

    def _repository(self) -> Repository:
        if self._repo is None:
            self._repo = Repository.open(self.project_root, author=load_identity())
        return self._repo
