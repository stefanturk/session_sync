"""The screen you land on.

The app opens here rather than on a project, so this is the front door and not
a fallback: your recent projects, one click each, and a way to find one that is
not in the list yet.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sessionsync.ui import hubsync
from sessionsync.ui.settings import Settings

ROW_HEIGHT = 48


class StartScreen(QWidget):
    project_chosen = Signal(object)  # Path
    open_requested = Signal()
    connect_requested = Signal()
    song_chosen = Signal(object, str)  # Hub, song name
    add_requested = Signal(object)  # Hub

    def __init__(self, settings: Settings, parent: QWidget | None = None, me: dict | None = None):
        super().__init__(parent)
        self.settings = settings
        self.me = me
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

        self.collab_label = QLabel("COLLABORATORS")
        self.collab_label.setObjectName("silkscreen")
        column.addWidget(self.collab_label)

        self.collab = QListWidget()
        self.collab.setObjectName("recentList")
        self.collab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.collab.itemActivated.connect(self._song_activated)
        self.collab.itemClicked.connect(self._song_activated)
        column.addWidget(self.collab)

        self.connect_button = QPushButton("CONNECT A SHARED FOLDER…")
        self.connect_button.setCursor(Qt.PointingHandCursor)
        self.connect_button.clicked.connect(self.connect_requested.emit)
        column.addWidget(self.connect_button)
        column.addSpacing(14)

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

        self._refresh_collaborators()

        empty = not projects
        self.recent.setVisible(not empty)
        self.recent_label.setVisible(not empty)
        if not empty:
            # Hug the content rather than leaving a tall empty box.
            self.recent.setFixedHeight(min(len(projects), 5) * ROW_HEIGHT + 14)

    def _refresh_collaborators(self) -> None:
        """One block per shared folder, a row per song inside it."""
        self.collab.clear()
        rows = 0
        for hub in self.settings.hubs():
            header = QListWidgetItem()
            header.setFlags(Qt.NoItemFlags)
            header.setSizeHint(QSize(0, 48))
            self.collab.addItem(header)
            self.collab.setItemWidget(header, _HubHeader(hub, self.add_requested.emit))
            rows += 1
            songs = hubsync.song_rows(hub, self.settings, self.me)
            for row in songs:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, (hub, row.song.name))
                item.setSizeHint(QSize(0, ROW_HEIGHT))
                self.collab.addItem(item)
                self.collab.setItemWidget(item, _SongRow(row))
                rows += 1
            if not songs:
                note = QListWidgetItem()
                note.setFlags(Qt.NoItemFlags)
                note.setSizeHint(QSize(0, 30))
                self.collab.addItem(note)
                label = QLabel("  No Ableton projects in this folder yet.")
                label.setObjectName("recentPath")
                self.collab.setItemWidget(note, label)
                rows += 1
        self.collab.setVisible(rows > 0)
        if rows:
            self.collab.setFixedHeight(min(rows, 8) * ROW_HEIGHT + 14)

    def _song_activated(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if data:
            hub, song = data
            self.song_chosen.emit(hub, song)

    def _chosen(self, item: QListWidgetItem) -> None:
        self.project_chosen.emit(Path(item.data(Qt.UserRole)))


class _HubHeader(QWidget):
    def __init__(self, hub, on_share):
        super().__init__()
        self.setFixedHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        name = QLabel(hub.name)
        name.setObjectName("recentName")
        row.addWidget(name, 1)
        share = QPushButton("Add a project…")
        share.setCursor(Qt.PointingHandCursor)
        share.setFixedHeight(34)
        share.setStyleSheet("padding: 2px 14px; font-size: 11px;")
        share.setMinimumWidth(150)
        share.clicked.connect(lambda: on_share(hub))
        row.addWidget(share)


class _SongRow(QWidget):
    """Song name, who did what last, and whether this Mac is behind."""

    def __init__(self, row: "hubsync.SongRow"):
        super().__init__()
        self.setFixedHeight(ROW_HEIGHT)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 5, 4, 5)
        column.setSpacing(1)

        top = QHBoxLayout()
        name = QLabel(row.song.name)
        name.setObjectName("recentName")
        top.addWidget(name, 1)
        state = QLabel(row.label)
        state.setObjectName("recentPath")
        top.addWidget(state)
        column.addLayout(top)

        detail = QLabel(_elide(row.detail or "No summary", 80))
        detail.setObjectName("recentPath")
        detail.setTextFormat(Qt.PlainText)
        column.addWidget(detail)


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
