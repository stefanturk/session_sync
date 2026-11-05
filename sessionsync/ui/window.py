"""The window: a shell around one tab per project.

It knows nothing about repositories — every project is a `ProjectPane` with its
own engine on its own thread. What this owns is the tab bar, the menus, and the
question asked before a project is closed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTabWidget,
)

from sessionsync.auth.identity import load_identity
from sessionsync.core.localstate import LocalState
from sessionsync.ui import hubsync, theme
from sessionsync.ui.notify import Notifier
from sessionsync.ui.pane import ProjectPane
from sessionsync.ui.presenter import close_warning
from sessionsync.ui.settings import Settings
from sessionsync.ui.start import StartScreen
from sessionsync.ui.widgets import dot_icon

APP_NAME = "SessionSync"

#: How often shared folders are checked for new songs and versions.
HUB_CHECK_MS = 60_000


class _HubCheck(QThread):
    """One pass over the shared folders, off the UI thread (it may download)."""

    done = Signal(list)

    def __init__(self, settings, author, open_roots, parent: QObject | None = None):
        super().__init__(parent)
        self.settings, self.author, self.open_roots = settings, author, open_roots

    def run(self) -> None:
        try:
            self.done.emit(hubsync.check_hubs(self.settings, self.author, self.open_roots))
        except Exception:  # noqa: BLE001 - the next pass tries again
            self.done.emit([])


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()

        self.setWindowTitle(APP_NAME)
        self.resize(880, 660)
        self.setMinimumSize(700, 540)
        self.setStyleSheet(theme.STYLESHEET)
        self.notifier = Notifier(on_click=self.show_news_target)
        self._hub_check: _HubCheck | None = None
        self._hub_timer: QTimer | None = None
        self._last_live_warning = None
        self._build()

    # --- construction ----------------------------------------------------

    @staticmethod
    def _me() -> dict | None:
        try:
            return load_identity()
        except Exception:  # noqa: BLE001 - the list still works without knowing who you are
            return None

    def _build(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.start = StartScreen(self.settings, me=self._me())
        self.start.open_requested.connect(self.choose_project)
        self.start.project_chosen.connect(self.open_project)
        self.start.connect_requested.connect(self.connect_folder)
        self.start.song_chosen.connect(self.open_song)
        self.start.add_requested.connect(self.add_project)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.start)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self._build_menu()
        self._show_start_if_empty()

    def _build_menu(self) -> None:
        open_project = QAction("&Open Project…", self)
        open_project.setShortcut(QKeySequence.Open)
        open_project.triggered.connect(self.choose_project)

        menu = self.menuBar().addMenu("&Project")
        menu.addAction(open_project)

        # Rebuilt every time it opens, so it never shows a folder that has gone.
        self.recent_menu = menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

        menu.addSeparator()
        close_tab = QAction("&Close Project", self)
        close_tab.setShortcut(QKeySequence.Close)  # ⌘W closes a tab, not the app
        close_tab.triggered.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        menu.addAction(close_tab)

        menu.addSeparator()
        history = QAction("Project &History…", self)
        history.triggered.connect(lambda: self._with_current(ProjectPane.show_history))
        menu.addAction(history)

        reveal = QAction("Reveal in &Finder", self)
        reveal.triggered.connect(lambda: self._with_current(ProjectPane.reveal))
        menu.addAction(reveal)

        for number in range(1, 10):
            jump = QAction(f"Project {number}", self)
            jump.setShortcut(QKeySequence(f"Ctrl+{number}"))
            jump.triggered.connect(lambda _=False, n=number: self._go_to_tab(n - 1))
            self.addAction(jump)

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        projects = self.settings.recent_projects()
        if not projects:
            empty = self.recent_menu.addAction("No recent projects")
            empty.setEnabled(False)
            return
        for project in projects:
            action = self.recent_menu.addAction(project.name)
            action.setToolTip(str(project))
            action.triggered.connect(lambda _=False, p=project: self.open_project(p))

    # --- projects --------------------------------------------------------

    def panes(self) -> list[ProjectPane]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def open_project(self, project_root: Path) -> ProjectPane | None:
        project_root = Path(project_root).expanduser().resolve()

        existing = self._pane_for(project_root)
        if existing is not None:
            self.tabs.setCurrentWidget(existing)
            return existing

        if not LocalState.exists(project_root):
            self._not_a_project(project_root)
            return None

        pane = ProjectPane(project_root)
        pane.notifier = self.notifier
        pane.state_changed.connect(lambda level, label, p=pane: self._tab_state(p, level, label))
        index = self.tabs.addTab(pane, pane.project_name)
        self.tabs.setTabIcon(index, dot_icon(theme.EDGE_LIT))
        self._style_close_button(index, pane)
        self.tabs.setCurrentIndex(index)

        self.settings.remember(project_root)
        self.start.refresh()
        self._show_start_if_empty()
        return pane

    # --- shared folders --------------------------------------------------

    def connect_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the shared folder (e.g. in Google Drive)"
        )
        if folder:
            self.settings.add_hub(Path(folder))
            self.start.refresh()

    def open_song(self, hub, song: str) -> None:
        """Open your copy of a shared song, getting it first if you do not have one."""
        local = self.settings.song_path(hub, song)
        if local.is_dir() and LocalState.exists(local):
            self.open_project(local)
            return

        found = hub.song(song)
        me = self._me() or {}
        working, _ = hubsync.others_on(found, me) if found else ([], [])
        if working:
            QMessageBox.information(
                self,
                f"{', '.join(working)} is working on it",
                f"{', '.join(working)} is working on {song} right now.\n\n"
                "Call each other first so you do not both record. You can still "
                "get a copy once they are done.",
            )
            return
        if found is not None and found.kind == "ready":
            answer = QMessageBox.question(
                self,
                "Set up this project?",
                f"'{song}' is a plain Ableton project in the shared folder.\n\n"
                "I will set it up for sharing (this only adds a hidden folder inside it; "
                "your files are not changed), then make your own copy on this Mac. "
                "If the folder is big, this reads all of it, which can take a while.",
                QMessageBox.Ok | QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                return

        parent = QFileDialog.getExistingDirectory(
            self,
            f"Where should your copy of {song} go on this Mac?",
            str(self.settings.local_root()),
        )
        if not parent:
            return
        destination = Path(parent) / song
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            local = hubsync.join(hub, song, self.settings, load_identity(), destination)
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            QMessageBox.warning(self, "Could not get that song", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.start.refresh()
        self.open_project(local)

    def add_project(self, hub) -> None:
        """Put a project from this Mac into a collaborator's shared folder."""
        folder = QFileDialog.getExistingDirectory(
            self, f"Choose the Ableton project to add to {hub.name}"
        )
        if not folder:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            path = hubsync.share(hub, Path(folder), self.settings, load_identity())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not add that project", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.start.refresh()
        self.open_project(path)

    def start_hub_watch(self) -> None:
        """Check the shared folders now and then every minute."""
        if self._hub_timer is None:
            self._hub_timer = QTimer(self)
            self._hub_timer.timeout.connect(self.check_hubs_now)
        self._hub_timer.start(HUB_CHECK_MS)
        QTimer.singleShot(2000, self.check_hubs_now)

    def check_hubs_now(self) -> None:
        if self._hub_check is not None and self._hub_check.isRunning():
            return
        if not self.settings.hubs():
            return
        try:
            author = load_identity()
        except Exception:  # noqa: BLE001
            return
        self._check_live(author)
        open_roots = {p.project_root for p in self.panes()}
        self._hub_check = _HubCheck(self.settings, author, open_roots, self)
        self._hub_check.done.connect(self._hub_news)
        self._hub_check.start()

    def _check_live(self, author: dict) -> None:
        """If Live just opened a set that someone else is on, say so once."""
        from sessionsync.core import livewatch

        try:
            document = livewatch.open_document()
            warning = hubsync.live_warning(self.settings, author, document)
        except Exception:  # noqa: BLE001 - best effort only
            return
        if warning is None:
            self._last_live_warning = None
            return
        if self._last_live_warning != (warning.song, warning.text):
            self._last_live_warning = (warning.song, warning.text)
            self.notifier.show(
                warning.text, title=warning.hub.name, target=(warning.hub, warning.song, warning.path)
            )

    def _hub_news(self, news: list) -> None:
        for item in news:
            self.notifier.show(item.text, title=item.hub.name, target=(item.hub, item.song, item.path))
        if news:
            self.start.refresh()

    def show_news_target(self, target) -> None:
        """A banner was clicked: go to the song it was about."""
        self.show()
        self.raise_()
        self.activateWindow()
        if isinstance(target, Path):
            self.open_project(target)
        elif isinstance(target, tuple):
            hub, song, path = target
            if path is not None and LocalState.exists(path):
                self.open_project(path)
            else:
                self.stack.setCurrentWidget(self.start)

    def choose_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a SessionSync project")
        if folder:
            self.open_project(Path(folder))

    def _style_close_button(self, index: int, pane: ProjectPane) -> None:
        """Qt's stock close button cannot be styled by a stylesheet, and its
        default look is a bright square that fights everything around it."""
        button = QPushButton("✕")
        button.setObjectName("tabClose")
        button.setFixedSize(15, 15)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(f"Close {pane.project_name}")
        button.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(pane)))
        self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, button)

    def _pane_for(self, project_root: Path) -> ProjectPane | None:
        """Two engines on one state.db could interleave their writes."""
        for pane in self.panes():
            if pane.project_root == project_root:
                return pane
        return None

    def _not_a_project(self, project_root: Path) -> None:
        from sessionsync.core.master import folder_kind

        if folder_kind(project_root) == "master":
            QMessageBox.information(
                self,
                "This is the shared copy",
                f"'{project_root.name}' is the shared copy in the shared folder. Nobody "
                "works in it directly.\n\nUse Get it on the collaborator list to make "
                "your own copy, work there, and put it back when you are done.",
            )
            return
        QMessageBox.information(
            self,
            "Not part of a shared folder yet",
            f"'{project_root.name}' is not set up with a collaborator yet.\n\n"
            "On the first screen, press Add a project… under the person you share with.",
        )

    # --- tab state -------------------------------------------------------

    def _tab_state(self, pane: ProjectPane, level: str, state_label: str) -> None:
        index = self.tabs.indexOf(pane)
        if index < 0:
            return
        accent, _ = theme.level_colours(level)
        self.tabs.setTabIcon(index, dot_icon(accent))
        self.tabs.setTabToolTip(index, f"{pane.project_name} — {state_label}")

    def _tab_changed(self, index: int) -> None:
        current = self.tabs.widget(index) if index >= 0 else None
        for pane in self.panes():
            pane.set_active(pane is current)
        self.setWindowTitle(
            f"{APP_NAME} — {current.project_name}" if current else APP_NAME
        )

    def _go_to_tab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _with_current(self, method) -> None:
        pane = self.tabs.currentWidget()
        if pane is not None:
            method(pane)

    def _show_start_if_empty(self) -> None:
        empty = self.tabs.count() == 0
        self.stack.setCurrentWidget(self.start if empty else self.tabs)
        if empty:
            self.start.refresh()
            self.setWindowTitle(APP_NAME)

    # --- closing ---------------------------------------------------------

    def close_tab(self, index: int) -> None:
        pane = self.tabs.widget(index)
        if pane is None:
            return

        if pane.has_unpushed_work:
            answer = self._confirm_close(pane)
            if answer == "cancel":
                return
            if answer == "push":
                self.tabs.setCurrentWidget(pane)
                pane.push()
                return

        pane.shutdown()
        self.tabs.removeTab(index)
        pane.deleteLater()
        self._show_start_if_empty()

    def _confirm_close(self, pane: ProjectPane) -> str:
        """Ask before walking away from work that only exists on this Mac."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setText(f"{pane.project_name} has changes that are not put back.")
        box.setInformativeText(close_warning(pane.snapshot.status) or "")
        push = box.addButton("Put Back First…", QMessageBox.AcceptRole)
        close = box.addButton("Close Anyway", QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is push:
            return "push"
        return "close" if clicked is close else "cancel"

    def _confirm_quit(self, panes: list[ProjectPane]) -> bool:
        names = "\n".join(f"   {pane.project_name}" for pane in panes)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setText(f"{len(panes)} projects have changes that are not put back.")
        box.setInformativeText(
            f"{names}\n\nQuitting does not delete anything — the files stay in "
            "their project folders — but the work is not backed up, and nobody "
            "else can see it."
        )
        quit_anyway = box.addButton("Quit Anyway", QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is quit_anyway

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        outstanding = [pane for pane in self.panes() if pane.has_unpushed_work]

        if len(outstanding) == 1:
            # One project: offer the same Push First… the tab close does.
            answer = self._confirm_close(outstanding[0])
            if answer == "cancel":
                event.ignore()
                return
            if answer == "push":
                self.tabs.setCurrentWidget(outstanding[0])
                outstanding[0].push()
                event.ignore()
                return
        elif outstanding and not self._confirm_quit(outstanding):
            event.ignore()
            return

        self.close_everything()
        super().closeEvent(event)

    def close_everything(self) -> None:
        """Stop every project's thread. Safe to call twice."""
        for pane in self.panes():
            pane.shutdown()
