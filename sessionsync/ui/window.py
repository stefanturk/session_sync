"""The window.

Layout and wiring only — what it *says* is decided in `presenter.py`, and what
it *does* happens on the engine thread. The freshness banner is deliberately the
largest thing on screen: the whole point is that you find out you are behind
when you sit down, not after two hours of takes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sessionsync.core.errors import (
    LocalChangesWouldBeReplacedError,
    NothingToPushError,
    ProjectBehindError,
    StalePushError,
)
from sessionsync.core.session import HEARTBEAT_SECONDS
from sessionsync.ui import theme
from sessionsync.ui.engine import Engine, Snapshot
from sessionsync.ui.notify import notify
from sessionsync.ui.presenter import human_size, local_time, notifications, screen
from sessionsync.ui.settings import Settings
from sessionsync.ui.widgets import Led

REFRESH_MS = 60_000

CHANGE_COLOURS = {
    "Added": theme.GOOD,
    "Modified": theme.VIOLET,
    "Removed": theme.BAD,
}


def _silkscreen(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("silkscreen")
    return label


class MainWindow(QMainWindow):
    request_refresh = Signal()
    request_heartbeat = Signal()
    request_start_session = Signal()
    request_end_session = Signal()
    request_push = Signal(str)
    request_pull = Signal(bool)
    request_review = Signal()
    request_history = Signal()

    def __init__(self, project_root: Path, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()
        self.previous_status = None
        self.snapshot: Snapshot | None = None

        self.setWindowTitle(f"SessionSync — {Path(project_root).name}")
        self.resize(860, 620)
        self.setMinimumSize(680, 520)
        self._build()
        self._start_engine(project_root)

    # --- construction ----------------------------------------------------

    def _build(self) -> None:
        self.setStyleSheet(theme.STYLESHEET)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 18)
        body.setSpacing(18)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._standing())
        body.addWidget(self._actions())
        body.addLayout(self._change_list(), stretch=1)

        self.last_push_label = QLabel()
        self.last_push_label.setObjectName("lastPush")
        body.addWidget(self.last_push_label)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Opening project…")

        self._build_menu()

    # --- the pieces ------------------------------------------------------

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
        column.addWidget(self.presence_label)

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

        self.pull_button = QPushButton()
        self.session_button = QPushButton()
        self.review_button = QPushButton()
        self.push_button = QPushButton()
        for button in self._buttons():
            button.setMinimumHeight(42)
            button.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(button)

        self.pull_button.clicked.connect(lambda: self.request_pull.emit(False))
        self.session_button.clicked.connect(self._toggle_session)
        self.review_button.clicked.connect(self.request_review.emit)
        self.push_button.clicked.connect(self._ask_for_note_and_push)
        return row

    def _change_list(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)

        heading = QHBoxLayout()
        heading.addWidget(_silkscreen("CHANGES"))
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

        self.nothing_changed = QLabel("Nothing has changed since your last push.")
        self.nothing_changed.setObjectName("summary")
        self.nothing_changed.setAlignment(Qt.AlignCenter)
        column.addWidget(self.nothing_changed, stretch=1)
        return column

    def _build_menu(self) -> None:
        history = QAction("Project &History…", self)
        history.triggered.connect(self.request_history.emit)
        open_project = QAction("&Open Project…", self)
        open_project.triggered.connect(self._open_another_project)
        reveal = QAction("Reveal in &Finder", self)
        reveal.triggered.connect(self._reveal)

        menu = self.menuBar().addMenu("&Project")
        menu.addAction(open_project)
        menu.addAction(history)
        menu.addAction(reveal)

    def _start_engine(self, project_root: Path) -> None:
        self.thread = QThread(self)
        self.engine = Engine(project_root)
        self.engine.moveToThread(self.thread)

        self.request_refresh.connect(self.engine.refresh)
        self.request_heartbeat.connect(self.engine.heartbeat)
        self.request_start_session.connect(self.engine.start_session)
        self.request_end_session.connect(self.engine.end_session)
        self.request_push.connect(self.engine.push)
        self.request_pull.connect(self.engine.pull)
        self.request_review.connect(self.engine.review)
        self.request_history.connect(self.engine.history)

        self.engine.snapshot_ready.connect(self._show)
        self.engine.operation_done.connect(self._operation_done)
        self.engine.operation_failed.connect(self._operation_failed)
        self.engine.busy_changed.connect(self._busy_changed)
        self.engine.checking.connect(self._checking)

        self.thread.start()
        self.request_refresh.emit()

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(HEARTBEAT_SECONDS * 1000)
        self.heartbeat_timer.timeout.connect(self.request_heartbeat.emit)
        self.heartbeat_timer.start()

    # --- drawing ---------------------------------------------------------

    def _show(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        model = screen(
            snapshot.status,
            project_name=snapshot.project_name,
            session_open=snapshot.session_open,
            last_push=snapshot.last_push,
        )

        self.title_label.setText(model.title)
        self.version_label.setText(model.version_line)
        self.state_label.setText(model.state_label)

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

        self._show_media(snapshot)
        for name, button, state in (
            ("pull", self.pull_button, model.pull),
            ("start", self.session_button, model.start),
            ("review", self.review_button, model.review),
            ("push", self.push_button, model.push),
        ):
            self._apply(button, state, primary=name == model.primary)

        self.changes.clear()
        for kind, path, size in model.changes:
            item = QTreeWidgetItem([kind.upper(), path, size])
            item.setForeground(0, QColor(CHANGE_COLOURS.get(kind, theme.DIM)))
            item.setForeground(1, QColor(theme.INK))
            item.setForeground(2, QColor(theme.DIM))
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.changes.addTopLevelItem(item)
        self.changes.setVisible(bool(model.changes))
        self.nothing_changed.setVisible(not model.changes)

        self.summary_label.setText(model.summary)
        self.last_push_label.setText(model.last_push)

        for message in notifications(self.previous_status, snapshot.status):
            notify(message, title=snapshot.project_name)
            self.statusBar().showMessage(message, 15000)
        self.previous_status = snapshot.status

    def _show_media(self, snapshot: Snapshot) -> None:
        parts = []
        if snapshot.media_outside:
            parts.append(
                f"⚠  {len(snapshot.media_outside)} audio file(s) live outside this project "
                "folder and would not reach your collaborators — run Collect All and Save "
                "in Ableton."
            )
        if snapshot.media_missing:
            parts.append(
                f"⚠  {len(snapshot.media_missing)} referenced file(s) cannot be found on "
                "this Mac at all."
            )
        self.media_label.setText("  ".join(parts))
        self.media_label.setVisible(bool(parts))

    @staticmethod
    def _apply(button: QPushButton, state, *, primary: bool = False) -> None:
        button.setText(state.label.upper())
        button.setEnabled(state.enabled)
        button.setToolTip(state.reason)
        button.setDefault(primary)
        if button.property("primary") != primary:
            button.setProperty("primary", primary)
            # Qt only re-reads a property selector when told the style is stale.
            button.style().unpolish(button)
            button.style().polish(button)

    def _busy_changed(self, message: str) -> None:
        """A push or a pull: nothing else should be clickable while it runs."""
        if message:
            self.statusBar().showMessage(message)
            for button in self._buttons():
                button.setEnabled(False)
        elif self.snapshot is not None:
            self._show(self.snapshot)

    def _checking(self, checking: bool) -> None:
        """A background scan. Say so, but leave the window usable."""
        if checking:
            self.statusBar().showMessage("Checking…")
        elif not self.statusBar().currentMessage().startswith("Checking"):
            return
        else:
            self.statusBar().clearMessage()

    def _buttons(self) -> tuple[QPushButton, ...]:
        return (self.pull_button, self.session_button, self.review_button, self.push_button)

    # --- actions ---------------------------------------------------------

    def _tick(self) -> None:
        self.request_refresh.emit()

    def _toggle_session(self) -> None:
        if self.snapshot and self.snapshot.session_open:
            self.request_end_session.emit()
        else:
            self.request_start_session.emit()

    def _ask_for_note_and_push(self) -> None:
        note, accepted = QInputDialog.getText(
            self, "Push Session", "What did you do this session?"
        )
        if accepted:
            self.request_push.emit(note.strip())

    def _open_another_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a SessionSync project")
        if not folder:
            return
        self.settings.remember(Path(folder))
        window = MainWindow(Path(folder), self.settings)
        window.show()
        self.close()

    def _reveal(self) -> None:
        if self.snapshot:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.snapshot.project_root)))

    # --- results ---------------------------------------------------------

    def _operation_done(self, name: str, result) -> None:
        if name == "push":
            self.statusBar().showMessage(
                f"Pushed v{result.version} — {human_size(result.uploaded_bytes)} uploaded.",
                15000,
            )
        elif name == "pull":
            if result.to_version == result.from_version:
                self.statusBar().showMessage("Already on the latest version.", 8000)
            else:
                self.statusBar().showMessage(f"Updated to v{result.to_version}.", 15000)
        elif name == "start_session":
            self.statusBar().showMessage("Session started. Go and make some noise.", 8000)
        elif name == "end_session":
            self.statusBar().showMessage("Session ended. Nothing was uploaded.", 8000)
        elif name == "review":
            self._show_review(result)
        elif name == "history":
            self._show_history(result)

    def _operation_failed(self, name: str, error: Exception) -> None:
        if isinstance(error, LocalChangesWouldBeReplacedError):
            self._confirm_replacing_local_work(error)
            return
        if isinstance(error, NothingToPushError):
            self.statusBar().showMessage(str(error), 8000)
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        if isinstance(error, (StalePushError, ProjectBehindError)):
            box.setText("Someone else pushed first.")
            box.setInformativeText(str(error))
        else:
            box.setText("That did not work.")
            box.setInformativeText(str(error))
        box.exec()
        self.request_refresh.emit()

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
            self.statusBar().showMessage(
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
        _show_text(self, "Changes since you started", "\n".join(lines))

    def _show_history(self, entries) -> None:
        lines = []
        for entry in entries:
            who = (entry.author or {}).get("name", "Someone")
            lines.append(
                f"v{entry.version}   {local_time(entry.created_at)}   {who} — "
                f"{entry.note or '(no note)'}"
            )
            lines.append(
                f"        {len(entry.added)} added, {len(entry.modified)} changed, "
                f"{len(entry.deleted)} removed"
            )
        _show_text(self, "Project History", "\n".join(lines) or "No versions yet.")

    # --- shutdown --------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.timer.stop()
        self.heartbeat_timer.stop()
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(event)


def _show_text(parent, title: str, text: str) -> None:
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
