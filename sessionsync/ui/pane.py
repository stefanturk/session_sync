"""One project's worth of window.

Layout and wiring only — what it *says* is decided in `presenter.py`, and what
it *does* happens on the engine thread. The freshness banner is deliberately the
largest thing on screen: the whole point is that you find out you are behind
when you sit down, not after two hours of takes.

This is a plain widget rather than a window so several projects can be open at
once, in tabs. Each pane owns its own `Engine` on its own thread, and the shell
around it knows nothing about repositories.
"""

from __future__ import annotations

from sessionsync.core import osinfo

from pathlib import Path

from PySide6.QtCore import (
    QFileSystemWatcher,
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionsync.core.errors import (
    DriveEditsError,
    FileInUseError,
    LocalChangesWouldBeReplacedError,
    NothingToPushError,
    ProjectBehindError,
    SharedHistoryMissingError,
    StalePushError,
)
from sessionsync.core.session import HEARTBEAT_SECONDS
from sessionsync.ui import theme
from sessionsync.ui.dialogs import ask_push, describe_catch_up
from sessionsync.ui.engine import Engine, Snapshot
from sessionsync.ui.notify import notify
from sessionsync.ui.presenter import CHANGE_HINTS, human_size, local_time, notifications, screen
from sessionsync.ui.widgets import Led

#: The tab you are looking at. Anything you do lands here within a minute.
ACTIVE_REFRESH_MS = 60_000
#: Tabs you are not looking at. Five large projects all rescanning every minute
#: is real disk churn for information nobody is reading.
BACKGROUND_REFRESH_MS = 180_000
#: After a save in Live, wait this long for it to finish writing, then look.
SAVE_SETTLE_MS = 2_000

CHANGE_COLOURS = {
    "Added": theme.GOOD,
    "Modified": theme.VIOLET,
    "Removed": theme.BAD,
}


def _silkscreen(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("silkscreen")
    return label


class ProjectPane(QWidget):
    request_refresh = Signal()
    request_heartbeat = Signal()
    request_open_live = Signal()
    request_rescue = Signal()
    request_push = Signal(str)
    request_pull = Signal(bool)
    request_review = Signal()
    request_history = Signal()
    request_restore = Signal(int, str)
    request_open_revision = Signal(int)
    request_make_latest = Signal(int)
    request_reshare = Signal()
    request_catch_up = Signal()

    #: level, state_label — so the shell can colour the tab and title the window
    state_changed = Signal(str, str)

    def __init__(self, project_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.previous_status = None
        #: set by the window so banners are clickable; None = plain banner
        self.notifier = None
        self.snapshot: Snapshot | None = None
        self._checking_now = False
        self._open_after_pull = False
        self._open_on_first_snapshot = False
        self._ask_after_live_closed = False
        self._busy = False
        self._push_note: str | None = None        # the note of the push in progress
        self._push_when_closed: str | None = None  # Live held a file: push once it lets go

        self._build()
        self._start_engine(self.project_root)

    # --- what the shell asks ---------------------------------------------

    @property
    def project_name(self) -> str:
        return self.project_root.name

    @property
    def session_open(self) -> bool:
        """Live has this song open (working is automatic: there is no button for it)."""
        return bool(self.snapshot and self.snapshot.live_open)

    @property
    def has_unpushed_work(self) -> bool:
        return bool(self.snapshot and self.snapshot.status.has_local_changes)

    def set_active(self, active: bool) -> None:
        """Foreground tabs check often; background tabs check rarely."""
        self.timer.setInterval(ACTIVE_REFRESH_MS if active else BACKGROUND_REFRESH_MS)
        if active:
            # Never land on a tab showing three-minute-old news.
            self.request_refresh.emit()

    def push(self) -> None:
        self._ask_for_note_and_push()

    def live_closed(self) -> None:
        """Live just closed this song (or quit): look again, then offer to push."""
        self._ask_after_live_closed = True
        self.request_refresh.emit()

    def open_in_live(self) -> None:
        self._open_clicked()

    def open_in_live_when_ready(self) -> None:
        """Open in Live as soon as this pane knows where the song stands (a launcher click)."""
        if self.snapshot is not None:
            self._open_clicked()
        else:
            self._open_on_first_snapshot = True

    def reveal(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project_root)))

    def show_history(self) -> None:
        self.request_history.emit()

    # --- construction ----------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 14)
        body.setSpacing(18)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._standing())
        body.addWidget(self._actions())
        body.addLayout(self._change_list(), stretch=1)

        self.last_push_label = QLabel()
        self.last_push_label.setObjectName("lastPush")
        body.addWidget(self.last_push_label)

        # The status line belongs to the project, not to the window: otherwise
        # a background tab's push overwrites the message for the tab you are
        # actually looking at.
        self.status_line = QLabel("Opening project…")
        self.status_line.setObjectName("statusLine")
        outer.addWidget(self.status_line)

        self._say_timer = QTimer(self)
        self._say_timer.setSingleShot(True)
        self._say_timer.timeout.connect(lambda: self.status_line.setText(""))

    def _header(self) -> QFrame:
        """Project name on the left, indicator light and status on the right."""
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 16, 24, 14)
        row.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(_silkscreen("PROJECT"))
        self.title_label = QLabel()
        self.title_label.setObjectName("projectName")
        left.addWidget(self.title_label)
        row.addLayout(left)
        row.addStretch(1)

        self.led = Led()
        self.state_label = QLabel()
        self.state_label.setObjectName("stateLabel")
        indicator = QHBoxLayout()
        indicator.setSpacing(7)
        indicator.addWidget(self.led)
        indicator.addWidget(self.state_label)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.addStretch(1)
        right.addLayout(indicator)
        self.version_label = QLabel()
        self.version_label.setObjectName("versions")
        self.version_label.setAlignment(Qt.AlignRight)
        self.version_label.setToolTip(
            "Latest: the newest version anyone pushed. You have: the version your copy is on."
        )
        right.addWidget(self.version_label)
        row.addLayout(right)
        return header

    def _standing(self) -> QFrame:
        """The readout: where you stand, who else is here, what would not travel."""
        self.banner_frame = QFrame()
        # QLabel is itself a QFrame, so this must be selected by name — an
        # unqualified QFrame rule puts a border around every label inside it.
        self.banner_frame.setObjectName("standing")
        column = QVBoxLayout(self.banner_frame)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(8)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        column.addWidget(self.banner)

        self.presence_label = QLabel()
        self.presence_label.setObjectName("presence")
        self.presence_label.setWordWrap(True)
        self.presence_label.setToolTip(
            "Who else is on this song. Working = they have it open in Live right now."
        )
        column.addWidget(self.presence_label)

        self.note_label = QLabel()  # "you changed 4 files while the app was closed…"
        self.note_label.setObjectName("presence")
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(False)
        column.addWidget(self.note_label)

        self.rescue_button = QPushButton("RESCUE EDITS")
        self.rescue_button.setCursor(Qt.PointingHandCursor)
        self.rescue_button.setMinimumHeight(36)
        self.rescue_button.setProperty("primary", True)
        self.rescue_button.clicked.connect(self._rescue_clicked)
        self.rescue_button.setVisible(False)
        column.addWidget(self.rescue_button, alignment=Qt.AlignLeft)

        self.media_label = QLabel()
        self.media_label.setWordWrap(True)
        self.media_label.setStyleSheet(f"color: {theme.WARN}; font-size: 11.5px;")
        column.addWidget(self.media_label)
        return self.banner_frame

    def _actions(self) -> QWidget:
        row = QWidget()
        buttons = QHBoxLayout(row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(9)

        self.open_button = QPushButton()
        self.pull_button = QPushButton()
        self.review_button = QPushButton()
        self.push_button = QPushButton()
        for button in self._buttons():
            button.setMinimumHeight(42)
            button.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(button)

        self.open_button.clicked.connect(self._open_clicked)
        self.pull_button.clicked.connect(self._pull_clicked)
        self.review_button.clicked.connect(self.request_review.emit)
        self.push_button.clicked.connect(self._ask_for_note_and_push)
        return row

    def _change_list(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)

        heading = QHBoxLayout()
        heading.addWidget(_silkscreen("CHANGES"))
        self.changes_hint = QLabel()
        self.changes_hint.setObjectName("summary")
        self.changes_hint.setStyleSheet(f"color: {theme.WARN};")
        heading.addWidget(self.changes_hint)
        heading.addStretch(1)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("summary")
        heading.addWidget(self.summary_label)
        column.addLayout(heading)

        self.changes = QTreeWidget()
        self.changes.setHeaderLabels(["CHANGE", "FILE", "SIZE"])
        self.changes.setRootIsDecorated(False)
        self.changes.setUniformRowHeights(True)
        self.changes.setFrameShape(QFrame.NoFrame)
        self.changes.setSelectionMode(QTreeWidget.NoSelection)
        self.changes.setFocusPolicy(Qt.NoFocus)
        self.changes.setColumnWidth(0, 100)
        self.changes.header().setStretchLastSection(False)
        self.changes.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.changes.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        column.addWidget(self.changes, stretch=1)

        self.nothing_changed = QLabel(
            "Nothing has changed since the version you are on.\n"
            "Saved changes in Live show up here within a few seconds."
        )
        self.nothing_changed.setObjectName("summary")
        self.nothing_changed.setAlignment(Qt.AlignCenter)
        column.addWidget(self.nothing_changed, stretch=1)
        return column

    def _start_engine(self, project_root: Path) -> None:
        self.thread = QThread(self)
        self.engine = Engine(project_root)
        self.engine.moveToThread(self.thread)

        self.request_refresh.connect(self.engine.refresh)
        self.request_heartbeat.connect(self.engine.heartbeat)
        self.request_open_live.connect(self.engine.open_live)
        self.request_rescue.connect(self.engine.rescue)
        self.request_push.connect(self.engine.push)
        self.request_pull.connect(self.engine.pull)
        self.request_review.connect(self.engine.review)
        self.request_history.connect(self.engine.history)
        self.request_restore.connect(self.engine.restore)
        self.request_open_revision.connect(self.engine.open_revision)
        self.request_make_latest.connect(self.engine.make_latest)
        self.request_reshare.connect(self.engine.reshare)
        self.request_catch_up.connect(self.engine.catch_up)

        self.engine.snapshot_ready.connect(self._show)
        self.engine.operation_done.connect(self._operation_done)
        self.engine.operation_failed.connect(self._operation_failed)
        self.engine.busy_changed.connect(self._busy_changed)
        self.engine.checking.connect(self._checking)

        self.thread.start()
        self.request_refresh.emit()

        self.timer = QTimer(self)
        self.timer.setInterval(ACTIVE_REFRESH_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        # Heartbeats do not slow down for a background tab: a late heartbeat
        # makes other people think you have stopped working, which is worse
        # than a late status.
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(HEARTBEAT_SECONDS * 1000)
        self.heartbeat_timer.timeout.connect(self.request_heartbeat.emit)
        self.heartbeat_timer.start()

        # A save in Live shows up in seconds, not at the next minute's check.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SAVE_SETTLE_MS)
        self._settle.timeout.connect(self.request_refresh.emit)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _path: self._settle.start())
        self.watcher.fileChanged.connect(lambda _path: self._settle.start())
        self._watch()

    def _watch(self) -> None:
        """Watch the folders the Live Sets are in (Live saves by replacing the file)."""
        folders = {str(self.project_root)}
        try:
            for live_set in self.project_root.glob("*.als"):
                folders.add(str(live_set.parent))
        except OSError:
            pass
        missing = [f for f in folders if f not in self.watcher.directories()]
        if missing:
            self.watcher.addPaths(missing)

    # --- drawing ---------------------------------------------------------

    def _show(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        model = screen(
            snapshot.status,
            project_name=snapshot.project_name,
            last_push=snapshot.last_push,
            live_open=snapshot.live_open,
            live_on_drive=snapshot.live_on_drive,
            set_summary=snapshot.set_summary,
        )
        self.model = model

        self.title_label.setText(model.title)
        self.version_label.setText(model.version_line)
        self.state_label.setText(model.state_label)
        self.state_label.setToolTip(model.state_hint)
        self.led.setToolTip(model.state_hint)

        accent, fill = theme.level_colours(model.banner.level)
        self.state_label.setStyleSheet(
            f"color: {accent}; font-size: 10px; font-weight: 600; letter-spacing: 2px;"
        )
        self.led.set_colour(accent)
        # Someone else pushing is the one thing you want to catch out of the
        # corner of your eye, since pushing over it is what wastes a take.
        self.led.set_pulsing(
            any(s.get("state") == "pushing" for s in snapshot.status.active_sessions)
        )

        self.banner.setText(model.banner.text)
        self.banner.setStyleSheet(
            f"color: {accent}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        self.banner_frame.setStyleSheet(
            f"QFrame#standing {{ background: {fill}; border: 1px solid {accent};"
            f" border-radius: {theme.RADIUS}px; }}"
        )

        self.presence_label.setText(
            "\n".join(f"●  {line}" for line in model.presence.splitlines())
        )
        self.presence_label.setVisible(bool(model.presence))
        if snapshot.late_note:
            self.note_label.setText(f"✓  {snapshot.late_note}")
            self.note_label.setVisible(True)
        self.rescue_button.setVisible(model.rescue is not None)
        if model.rescue is not None:
            self.rescue_button.setToolTip(model.rescue.tooltip)

        self._show_media(snapshot)
        for name, button, state in (
            ("open", self.open_button, model.open),
            ("pull", self.pull_button, model.pull),
            ("review", self.review_button, model.review),
            ("push", self.push_button, model.push),
        ):
            self._apply(button, state, primary=name == model.primary)
        self.changes_hint.setText(model.changes_hint)

        self.changes.clear()
        for kind, path, size in model.changes:
            item = QTreeWidgetItem([kind.upper(), path, size])
            item.setForeground(0, QColor(CHANGE_COLOURS.get(kind, theme.DIM)))
            item.setForeground(1, QColor(theme.INK))
            item.setForeground(2, QColor(theme.DIM))
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            item.setToolTip(0, CHANGE_HINTS.get(kind, ""))
            item.setToolTip(1, f"{path}\n{CHANGE_HINTS.get(kind, '')}")
            self.changes.addTopLevelItem(item)
        self.changes.setVisible(bool(model.changes))
        self.nothing_changed.setVisible(not model.changes)

        self.summary_label.setText(model.summary)
        self.last_push_label.setText(model.last_push)

        for message in notifications(self.previous_status, snapshot.status):
            if self.notifier is not None:
                self.notifier.show(message, title=snapshot.project_name, target=self.project_root)
            else:
                notify(message, title=snapshot.project_name)
            self._say(message, 15000)
        self.previous_status = snapshot.status

        self.state_changed.emit(model.banner.level, model.state_label)
        self._watch()
        if self._open_on_first_snapshot:
            self._open_on_first_snapshot = False
            QTimer.singleShot(0, self._open_clicked)
        if self._push_when_closed is not None and not snapshot.live_open and not self._busy:
            note, self._push_when_closed = self._push_when_closed, None
            self._ask_after_live_closed = False  # already said yes: no second question
            if snapshot.status.has_local_changes and snapshot.status.state != "push_blocked":
                self._say("You closed the song in Live — pushing now, as promised.", 10000)
                self._send_push(note)
        if self._ask_after_live_closed and not snapshot.live_open:
            self._ask_after_live_closed = False
            QTimer.singleShot(0, self._after_live_closed)

    def _show_media(self, snapshot: Snapshot) -> None:
        parts = []
        for problem in snapshot.problems:
            parts.append(f"✕  {problem}")
        if snapshot.media_missing:
            parts.append(
                f"⚠  {len(snapshot.media_missing)} file(s) the song uses cannot be found on "
                "this computer at all."
            )
        self.media_label.setText("  ".join(parts))
        self.media_label.setVisible(bool(parts))

    @staticmethod
    def _apply(button: QPushButton, state, *, primary: bool = False) -> None:
        button.setText(state.label.upper())
        button.setEnabled(state.enabled)
        button.setToolTip(state.tooltip)
        button.setDefault(primary)
        if button.property("primary") != primary:
            button.setProperty("primary", primary)
            # Qt only re-reads a property selector when told the style is stale.
            button.style().unpolish(button)
            button.style().polish(button)

    def _say(self, message: str, ms: int = 0) -> None:
        self.status_line.setText(message)
        self._say_timer.stop()
        if ms:
            self._say_timer.start(ms)

    def _busy_changed(self, message: str) -> None:
        """A push or a pull: nothing else should be clickable while it runs."""
        self._busy = bool(message)
        if message:
            self._say(message)
            for button in self._buttons():
                button.setEnabled(False)
        elif self.snapshot is not None:
            self._show(self.snapshot)

    def _checking(self, checking: bool) -> None:
        """A background scan. Say so, but leave the window usable."""
        if checking:
            self._checking_now = True
            self._say("Checking…")
        elif self._checking_now:
            self._checking_now = False
            self._say("")

    def _buttons(self) -> tuple[QPushButton, ...]:
        return (self.open_button, self.pull_button, self.review_button, self.push_button,
                self.rescue_button)

    # --- actions ---------------------------------------------------------

    def _tick(self) -> None:
        self.request_refresh.emit()

    def _open_clicked(self) -> None:
        """Open your copy in Live — after a Pull, if the song has moved on."""
        snapshot = self.snapshot
        behind = snapshot.status.versions_behind if snapshot else 0
        if snapshot is not None and snapshot.live_on_drive:
            QMessageBox.warning(
                self, "Close the shared copy first",
                "Live has the SHARED copy in Google Drive open. Close that set in Live "
                "(don't save), then press Open in Live again.",
            )
            return
        if snapshot is not None and snapshot.status.drive_edits:
            QMessageBox.information(
                self, "Rescue the edit first",
                "Someone saved this song inside Google Drive. Press Rescue Edits first, so "
                "that save is not lost, then open the song.",
            )
            return
        if snapshot is not None and not self._others_ok(snapshot):
            return
        if snapshot is None or not behind:
            self.request_open_live.emit()
            return
        if snapshot.status.state == "push_blocked":
            # Your unpushed work and their newer version: Catch Up decides, not a quick open.
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setText("Someone pushed while you were working.")
            box.setInformativeText(
                "Catch Up first: it keeps a full copy of your version and brings theirs in. "
                "Opening now means working on top of an old version."
            )
            catch = box.addButton("Catch Up…", QMessageBox.AcceptRole)
            anyway = box.addButton("Open Anyway", QMessageBox.DestructiveRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is catch:
                self._pull_clicked()
            elif box.clickedButton() is anyway:
                self.request_open_live.emit()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        versions = f"{behind} version{'s' if behind != 1 else ''}"
        box.setText(f"{self.project_name} is {versions} behind.")
        box.setInformativeText("Pull the latest first, so you work on top of what everyone else did?")
        pull_open = box.addButton("Pull && Open", QMessageBox.AcceptRole)
        anyway = box.addButton("Open Anyway", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(pull_open)
        box.exec()
        if box.clickedButton() is pull_open:
            self._open_after_pull = True
            self.request_pull.emit(False)
        elif box.clickedButton() is anyway:
            self.request_open_live.emit()

    def _others_ok(self, snapshot) -> bool:
        """Someone else working on it right now: say so before you both record."""
        from sessionsync.core.session import is_working

        working = [s.get("display_name") or "Someone" for s in snapshot.status.active_sessions
                   if is_working(s)]
        if not working or snapshot.live_open:
            return True
        who = ", ".join(dict.fromkeys(working))
        answer = QMessageBox.question(
            self, f"{who} is working on it",
            f"{who} has {self.project_name} open right now. Call each other first, so "
            "you don't both change it — only one of you can push without catching up.\n\n"
            "Open it anyway?",
            QMessageBox.Open | QMessageBox.Cancel,
        )
        return answer == QMessageBox.Open

    def _rescue_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "Rescue the edit made in Drive",
            "Someone opened the song straight from the shared folder in Google Drive and "
            "saved it there.\n\nRescue brings that save into your copy (beside your own "
            "set if you have changes of your own), and puts the shared copy back the way "
            "it was pushed. Nothing is deleted.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Ok:
            self.request_rescue.emit()

    def _pull_clicked(self) -> None:
        state = self.snapshot.status.state if self.snapshot else ""
        if state != "push_blocked":
            self.request_pull.emit(False)
            return
        answer = QMessageBox.question(
            self,
            "Catch up",
            "Someone pushed a newer version while you were working.\n\n"
            "I will save a full copy of YOUR version next to this project first, then "
            "bring theirs in. Nothing is pushed until you say so.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Ok:
            self.request_catch_up.emit()

    def _after_live_closed(self) -> None:
        """You closed the song in Live: push now? (or catch up, if someone pushed first)."""
        snapshot = self.snapshot
        if snapshot is None or self._busy or snapshot.live_open:
            return
        status = snapshot.status
        if not status.has_local_changes or status.drive_edits:
            return  # nothing to push; or Rescue comes first, and its banner says so
        window = self.window()
        if hasattr(window, "focus_pane"):
            window.focus_pane(self)
        if status.state == "push_blocked":
            if self._confirm_catch_up_after_close():
                self.request_catch_up.emit()
            return
        note = ask_push(self, snapshot, live_closed=True)
        if note is not None:
            self._send_push(note)

    def _confirm_catch_up_after_close(self) -> bool:
        answer = QMessageBox.question(
            self,
            f"You closed {self.project_name}",
            f"You closed {self.project_name} in Live — but someone pushed a newer "
            "version while you were working, so yours can't go up as it is.\n\n"
            "Catch Up keeps a full copy of YOUR version next to the project, brings "
            "theirs in, and puts your set beside theirs as \"(mine)\" so you can "
            "combine them in Live. Nothing is pushed until you say so.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        return answer == QMessageBox.Ok

    def _ask_for_note_and_push(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        note = ask_push(self, snapshot)
        if note is not None:
            self._send_push(note)

    def _send_push(self, note: str) -> None:
        self._push_note = note
        self.request_push.emit(note)

    # --- results ---------------------------------------------------------

    def _operation_done(self, name: str, result) -> None:
        if name == "push":
            self._say(
                f"Pushed as v{result.version} — {human_size(result.uploaded_bytes)} sent.",
                15000,
            )
        elif name == "pull":
            if result.to_version == result.from_version:
                self._say("Already on the latest version.", 8000)
            else:
                self._say(f"Updated to v{result.to_version}.", 15000)
            if self._open_after_pull:
                self._open_after_pull = False
                self.request_open_live.emit()
        elif name == "catch_up":
            self._say(f"Caught up to v{result.pulled.to_version}.", 15000)
            QMessageBox.information(self, "You are caught up", describe_catch_up(result))
        elif name == "open_live":
            self._say(f"Opening {result.name} in Live — your own copy.", 10000)
            osinfo.open_in_live(result)
        elif name == "rescue":
            self._show_rescued(result)
        elif name == "review":
            self._show_review(result)
        elif name == "history":
            self._show_history(result)
        elif name == "open_revision":
            self._say(f"Opening {result.name} in Live.", 15000)
            osinfo.open_in_live(result)
        elif name == "reshare":
            self._reshare_offered = False
            self._say(f"{self.project_name} is shared again from your copy (v{result.base_version}).", 15000)
        elif name == "make_latest":
            self._say(f"Shared v{result.version}, the song as it was before. Updating your copy…", 15000)
            self.request_pull.emit(False)
        elif name == "restore":
            self._say(f"Restored a copy to {result}.", 15000)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.parent)))

    def _show_rescued(self, rescued) -> None:
        if not rescued:
            self._say("Nothing to rescue — someone already did.", 10000)
            return
        lines = []
        for item in rescued:
            if item.replaced:
                lines.append(f"• {item.path.name} now has the edit. Push when you are happy with it.")
            else:
                lines.append(
                    f"• Saved beside your set as “{item.path.name}”. Open it in Live to "
                    "compare, keep what you want, then delete it."
                )
        QMessageBox.information(
            self, "Edit rescued", "\n".join(lines) + "\n\nThe shared copy is back the way it was pushed."
        )

    def _operation_failed(self, name: str, error: Exception) -> None:
        if isinstance(error, SharedHistoryMissingError):
            self._offer_reshare(error)
            return
        if name == "refresh":
            # A background look runs every few seconds: a dialog each time would
            # never stop. Say it in the window, once, and keep looking.
            osinfo.log(f"refresh failed for {self.project_root}: {error!r}")
            self._say(f"Could not check this song just now: {error}")
            return
        self._open_after_pull = False
        if isinstance(error, LocalChangesWouldBeReplacedError):
            self._confirm_replacing_local_work(error)
            return
        if isinstance(error, NothingToPushError):
            self._say(str(error), 8000)
            return
        if name == "push" and isinstance(error, FileInUseError) and self.snapshot and self.snapshot.live_open:
            # Windows: Live still holds a new recording. Not an error worth a dialog:
            # the push goes by itself the moment Live lets go of the song.
            self._push_when_closed = self._push_note or ""
            held = getattr(error.path, "name", str(error.path))
            self._say(
                f"Live is still holding {held}, so it cannot be sent yet. Session Sync "
                "pushes by itself as soon as you close the song in Live.",
                0,
            )
            osinfo.log(f"push waits for Live to let go of {error.path}")
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        if isinstance(error, (StalePushError, ProjectBehindError)):
            box.setText("Someone else pushed first.")
            box.setInformativeText(str(error))
        elif isinstance(error, DriveEditsError):
            box.setText("The song was saved inside Google Drive.")
            box.setInformativeText(str(error))
        else:
            box.setText("That did not work.")
            box.setInformativeText(str(error))
        box.exec()
        self.request_refresh.emit()

    def _offer_reshare(self, error: SharedHistoryMissingError) -> None:
        """The song's hidden history in Drive is gone: offer to give it back, once."""
        self._say(str(error))
        if getattr(self, "_reshare_offered", False):
            return
        self._reshare_offered = True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setText(f"The shared copy of {self.project_name} has lost its history.")
        box.setInformativeText(
            "The hidden .sessionsync folder inside it in Google Drive is gone. If someone "
            "moved or deleted it by mistake, putting it back from Drive's Trash is best.\n\n"
            "Otherwise Session Sync can share it again from your copy: same song, same "
            "version numbers, and only files missing from Drive are uploaded. Your "
            "collaborators carry on as usual."
        )
        again = box.addButton("Share Again From My Copy", QMessageBox.AcceptRole)
        box.addButton("Not Now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is again:
            self.request_reshare.emit()

    def _confirm_replacing_local_work(self, error: LocalChangesWouldBeReplacedError) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setText("The new version changes files you have edited but not pushed.")
        box.setInformativeText(
            "\n".join(error.paths)
            + "\n\nYour versions would be moved into .sessionsync/trash, "
            "but they would leave the project."
        )
        replace = box.addButton("Replace My Versions", QMessageBox.DestructiveRole)
        box.addButton("Keep My Work", QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()

        if box.clickedButton() is replace:
            self.request_pull.emit(True)
        else:
            self._say(
                "Nothing was changed. Push your work first if you want to keep it.", 12000
            )

    def _show_review(self, diff) -> None:
        if not diff.has_changes:
            QMessageBox.information(self, "Review Changes", "Nothing has changed yet.")
            return
        lines = []
        for title, paths in (
            ("Added", diff.added),
            ("Modified", diff.modified),
            ("Removed", diff.deleted),
        ):
            if not paths:
                continue
            lines.append(title)
            for path in paths:
                size = diff.sizes.get(path)
                lines.append(f"   {path}" + (f"   {human_size(size)}" if size else ""))
            lines.append("")
        lines.append(f"Upload: {human_size(diff.changed_bytes)}")
        show_text(self, "Changes since you started", "\n".join(lines))

    def _show_history(self, entries) -> None:
        from sessionsync.ui.history import HistoryDialog

        dialog = HistoryDialog(self, entries, self.project_name)
        dialog.exec()
        if dialog.chosen is None:
            return
        action, version = dialog.chosen
        if action == "open":
            self.request_open_revision.emit(version)
        elif action == "latest":
            self.request_make_latest.emit(version)
        else:
            self.restore_version(version)

    def restore_version(self, version: int) -> None:
        """Rebuild an old version in a new folder beside the project."""
        parent = QFileDialog.getExistingDirectory(
            self, f"Where should the copy of v{version} go?", str(self.project_root.parent)
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.project_name} (v{version})"
        n = 2
        while destination.exists():
            destination = Path(parent) / f"{self.project_name} (v{version}) {n}"
            n += 1
        self.request_restore.emit(version, str(destination))

    # --- shutdown --------------------------------------------------------

    def shutdown(self, *, end_session: bool = True) -> None:
        """Stop this project cleanly. Called by the shell when a tab closes."""
        self.timer.stop()
        self.heartbeat_timer.stop()

        if end_session:
            self._end_session_before_closing()

        self.thread.quit()
        self.thread.wait(3000)

    def _end_session_before_closing(self) -> None:
        """Leave a true presence note behind, rather than a stale "working".

        Closing a tab ends nothing: with Live still open on the song it says
        "working", with unpushed changes it says so, and otherwise the note is
        removed. The engine decides from the repository, never from
        `self.snapshot`, which can be minutes old on a background tab.

        Emitting a signal and quitting the thread would race — the queued slot
        may never run — so this one call is synchronous. It is a small file
        write on the way out.
        """
        if not self.thread.isRunning() or self.thread is QThread.currentThread():
            return
        QMetaObject.invokeMethod(self.engine, "close_session", Qt.BlockingQueuedConnection)


def show_text(parent, title: str, text: str) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(620, 460)
    layout = QVBoxLayout(dialog)
    view = QTextEdit()
    view.setReadOnly(True)
    view.setPlainText(text)
    view.setStyleSheet("font-family: 'SF Mono', Menlo, monospace; font-size: 12px;")
    layout.addWidget(view)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
