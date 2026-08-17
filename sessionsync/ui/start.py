"""The screen you land on.

The app opens here rather than on a project, so this is the front door and not
a fallback: your recent projects, one click each, and a way to find one that is
not in the list yet.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sessionsync.ui.settings import Settings

ROW_HEIGHT = 48


class StartScreen(QWidget):
    project_chosen = Signal(object)  # Path
    open_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._build()

    def _build(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(64, 48, 64, 44)
        column.setSpacing(10)
        column.addStretch(1)

        wordmark = QLabel("SessionSync")
        wordmark.setObjectName("wordmark")
        wordmark.setAlignment(Qt.AlignCenter)
        column.addWidget(wordmark)

        tagline = QLabel("Pull, work, push — for Ableton projects.")
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignCenter)
        column.addWidget(tagline)
        column.addSpacing(26)

        self.recent_label = QLabel("RECENT PROJECTS")
        self.recent_label.setObjectName("silkscreen")
        column.addWidget(self.recent_label)

        self.recent = QListWidget()
        self.recent.setObjectName("recentList")
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.itemActivated.connect(self._chosen)
        self.recent.itemClicked.connect(self._chosen)
        column.addWidget(self.recent)

        self.open_button = QPushButton("OPEN PROJECT…")
        self.open_button.setMinimumHeight(44)
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self.open_requested.emit)
        column.addSpacing(6)
        column.addWidget(self.open_button)
        column.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list. Folders that have gone are already filtered out."""
        self.recent.clear()
        projects = self.settings.recent_projects()

        for project in projects:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(project))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.recent.addItem(item)
            self.recent.setItemWidget(item, _RecentRow(project))

        empty = not projects
        self.recent.setVisible(not empty)
        self.recent_label.setVisible(not empty)
        if not empty:
            # Hug the content rather than leaving a tall empty box.
            self.recent.setFixedHeight(min(len(projects), 5) * ROW_HEIGHT + 14)

    def _chosen(self, item: QListWidgetItem) -> None:
        self.project_chosen.emit(Path(item.data(Qt.UserRole)))


class _RecentRow(QWidget):
    """Project name over the folder it lives in, so two projects with the same
    name in different places are still telling apart."""

    def __init__(self, project: Path):
        super().__init__()
        # Fixed rather than measured: a row built outside the styled list is
        # sized with the default font and comes out too short, clipping itself.
        self.setFixedHeight(ROW_HEIGHT)
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 5, 4, 5)
        column.setSpacing(1)

        name = QLabel(project.name)
        name.setObjectName("recentName")
        column.addWidget(name)

        where = QLabel(_pretty_parent(project))
        where.setObjectName("recentPath")
        where.setTextFormat(Qt.PlainText)
        column.addWidget(where)


def _pretty_parent(project: Path) -> str:
    """Where it lives, with the home folder shortened the way a Mac shows it."""
    parent = project.parent
    try:
        text = f"~/{parent.relative_to(Path.home())}"
    except ValueError:
        text = str(parent)
    return _elide(text)


def _elide(text: str, limit: int = 64) -> str:
    """Keep the start and the end: the middle of a long path says least."""
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    return f"{text[:keep]}…{text[-keep:]}"
