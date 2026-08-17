"""The window: a shell around one tab per project.

It knows nothing about repositories — every project is a `ProjectPane` with its
own engine on its own thread. What this owns is the tab bar, the menus, and the
question asked before a project is closed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTabWidget,
)

from sessionsync.core.localstate import LocalState
from sessionsync.ui import theme
from sessionsync.ui.pane import ProjectPane
from sessionsync.ui.presenter import close_warning
from sessionsync.ui.settings import Settings
from sessionsync.ui.start import StartScreen
from sessionsync.ui.widgets import dot_icon

APP_NAME = "SessionSync"


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()

        self.setWindowTitle(APP_NAME)
        self.resize(880, 660)
        self.setMinimumSize(700, 540)
        self.setStyleSheet(theme.STYLESHEET)
        self._build()

    # --- construction ----------------------------------------------------

    def _build(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.start = StartScreen(self.settings)
        self.start.open_requested.connect(self.choose_project)
        self.start.project_chosen.connect(self.open_project)

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
        pane.state_changed.connect(lambda level, label, p=pane: self._tab_state(p, level, label))
        index = self.tabs.addTab(pane, pane.project_name)
        self.tabs.setTabIcon(index, dot_icon(theme.EDGE_LIT))
        self._style_close_button(index, pane)
        self.tabs.setCurrentIndex(index)

        self.settings.remember(project_root)
        self.start.refresh()
        self._show_start_if_empty()
        return pane

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
        QMessageBox.warning(
            self,
            "Not set up yet",
            f"'{project_root.name}' is not synced with SessionSync yet.\n\n"
            "Set it up from the command line first:\n"
            f"    sessionsync init '{project_root}' --remote <where the repository lives>",
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
        box.setText(f"{pane.project_name} has unpushed work.")
        box.setInformativeText(close_warning(pane.snapshot.status) or "")
        push = box.addButton("Push First…", QMessageBox.AcceptRole)
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
        box.setText(f"{len(panes)} projects have unpushed work.")
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
