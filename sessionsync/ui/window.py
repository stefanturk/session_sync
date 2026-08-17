"""The window.

Layout and wiring only — what it *says* is decided in `presenter.py`, and what
it *does* happens on the engine thread. The freshness banner is deliberately the
largest thing on screen: the whole point is that you find out you are behind
when you sit down, not after two hours of takes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction
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
from sessionsync.ui.engine import Engine, Snapshot
from sessionsync.ui.notify import notify
from sessionsync.ui.presenter import human_size, local_time, notifications, screen
from sessionsync.ui.settings import Settings

REFRESH_MS = 60_000

BANNER_COLOURS = {
    "ok": ("#1f5c34", "#d6f0de"),
    "info": ("#1f4a6b", "#d8ecfa"),
    "warning": ("#8a5a00", "#ffeccc"),
    "danger": ("#7d1f1f", "#ffdcdc"),
}

PRIMARY_BUTTON = """
QPushButton { background: #2f6fd0; color: white; border: none;
              border-radius: 7px; font-weight: 600; }
QPushButton:hover  { background: #2a63bb; }
QPushButton:pressed { background: #24549e; }
QPushButton:disabled { background: palette(button); color: palette(mid); }
"""


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
        self.resize(760, 560)
        self._build()
        self._start_engine(project_root)

    # --- construction ----------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 26px; font-weight: 600;")
        layout.addWidget(self.title_label)

        self.version_label = QLabel()
        self.version_label.setStyleSheet("font-size: 13px; color: palette(mid);")
        layout.addWidget(self.version_label)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setFrameShape(QFrame.StyledPanel)
        layout.addWidget(self.banner)

        self.presence_label = QLabel()
        self.presence_label.setWordWrap(True)
        self.presence_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.presence_label)

        self.media_label = QLabel()
        self.media_label.setWordWrap(True)
        self.media_label.setStyleSheet("font-size: 12px; color: #8a5a00;")
        self.media_label.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.media_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.pull_button = QPushButton()
        self.session_button = QPushButton()
        self.review_button = QPushButton()
        self.push_button = QPushButton()
        for button in (self.pull_button, self.session_button, self.review_button, self.push_button):
            button.setMinimumHeight(38)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.pull_button.clicked.connect(lambda: self.request_pull.emit(False))
        self.session_button.clicked.connect(self._toggle_session)
        self.review_button.clicked.connect(self.request_review.emit)
        self.push_button.clicked.connect(self._ask_for_note_and_push)

        self.changes = QTreeWidget()
        self.changes.setHeaderLabels(["Change", "File", "Size"])
        self.changes.setRootIsDecorated(False)
        self.changes.setUniformRowHeights(True)
        self.changes.setFrameShape(QFrame.NoFrame)
        self.changes.setSelectionMode(QTreeWidget.NoSelection)
        self.changes.setFocusPolicy(Qt.NoFocus)
        self.changes.setColumnWidth(0, 90)
        self.changes.header().setStretchLastSection(False)
        self.changes.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.changes.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.changes.setStyleSheet("QTreeWidget { font-size: 12px; }")
        layout.addWidget(self.changes, stretch=1)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        layout.addWidget(self.summary_label)

        self.last_push_label = QLabel()
        self.last_push_label.setStyleSheet("font-size: 12px; color: palette(mid);")
        layout.addWidget(self.last_push_label)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Opening project…")

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

        fg, bg = BANNER_COLOURS[model.banner.level]
        self.banner.setText(model.banner.text)
        self.banner.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 8px;"
            "padding: 14px 16px; font-size: 16px; font-weight: 600;"
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
            self.changes.addTopLevelItem(QTreeWidgetItem([kind, path, size]))
        self.changes.setVisible(bool(model.changes))

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
        button.setText(state.label)
        button.setEnabled(state.enabled)
        button.setToolTip(state.reason)
        button.setProperty("primary", primary)
        button.setDefault(primary)
        button.setStyleSheet(PRIMARY_BUTTON if primary else "")

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
            import subprocess

            subprocess.run(["open", str(self.snapshot.project_root)], check=False)

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
