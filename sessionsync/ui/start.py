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
    live_requested = Signal(object, str)  # Hub, song name: open it in Live straight away
    add_requested = Signal(object)  # Hub
    activity_action = Signal(object, str)  # Activity, "open" | "pull" | "history"
    alerts_requested = Signal()  # keep notifications on screen (macOS settings)
    remove_requested = Signal(object, str, object)  # Hub or None, song name, your copy or None
    remove_hub_requested = Signal(object)  # Hub
    unhide_requested = Signal(object)  # Hub: show its removed songs again
    invite_requested = Signal(object)  # Hub
    connect_found = Signal(object)  # Path of a shared folder found in Drive

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

        self.join_box = QVBoxLayout()
        self.join_box.setSpacing(4)
        column.addLayout(self.join_box)

        self.collab_label = QLabel("COLLABORATORS")
        self.collab_label.setObjectName("silkscreen")
        column.addWidget(self.collab_label)

        self.collab = QListWidget()
        self.collab.setObjectName("recentList")
        self.collab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.collab.itemActivated.connect(self._song_activated)
        self.collab.itemClicked.connect(self._song_activated)
        self.collab.setContextMenuPolicy(Qt.CustomContextMenu)
        self.collab.customContextMenuRequested.connect(self._collab_menu)
        column.addWidget(self.collab)

        self.connect_button = QPushButton("CONNECT A SHARED FOLDER…")
        self.connect_button.setToolTip(
            "Choose the Google Drive folder you share with someone (the one holding the song folders)."
        )
        self.connect_button.setCursor(Qt.PointingHandCursor)
        self.connect_button.clicked.connect(self.connect_requested.emit)
        column.addWidget(self.connect_button)
        column.addSpacing(14)

        heading = QHBoxLayout()
        self.activity_label = QLabel("ACTIVITY")
        self.activity_label.setObjectName("silkscreen")
        heading.addWidget(self.activity_label, 1)
        self.alerts_button = QPushButton("Keep notifications on screen…")
        self.alerts_button.setFlat(True)
        self.alerts_button.setCursor(Qt.PointingHandCursor)
        self.alerts_button.setStyleSheet("font-size: 11px; padding: 0 4px; text-decoration: underline;")
        self.alerts_button.setToolTip(
            "Opens System Settings › Notifications › SessionSync: choose “Alerts” so a "
            "notification stays until you click it."
        )
        self.alerts_button.clicked.connect(self.alerts_requested.emit)
        heading.addWidget(self.alerts_button)
        column.addLayout(heading)
        self.activity = QVBoxLayout()
        self.activity.setSpacing(2)
        column.addLayout(self.activity)
        column.addSpacing(14)

        self.recent_label = QLabel("RECENT PROJECTS")
        self.recent_label.setObjectName("silkscreen")
        column.addWidget(self.recent_label)

        self.recent = QListWidget()
        self.recent.setObjectName("recentList")
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.itemActivated.connect(self._chosen)
        self.recent.itemClicked.connect(self._chosen)
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_menu)
        column.addWidget(self.recent)

        self.open_button = QPushButton("OPEN PROJECT…")
        self.open_button.setToolTip("Open your copy of a song that is already set up on this computer.")
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
        self._refresh_activity()
        self._refresh_join()

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
            header.setFlags(Qt.ItemIsEnabled)
            header.setData(Qt.UserRole + 1, hub)
            header.setSizeHint(QSize(0, 48))
            self.collab.addItem(header)
            self.collab.setItemWidget(header, _HubHeader(hub, self.add_requested.emit, self.invite_requested.emit))
            rows += 1
            songs = hubsync.song_rows(hub, self.settings, self.me)
            for row in songs:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, (hub, row.song.name))
                item.setSizeHint(QSize(0, ROW_HEIGHT))
                self.collab.addItem(item)
                self.collab.setItemWidget(
                    item, _SongRow(row, lambda h=hub, n=row.song.name: self.live_requested.emit(h, n))
                )
                rows += 1
            hidden = self.settings.hidden_songs(hub)
            if hidden:
                note = QListWidgetItem()
                note.setFlags(Qt.ItemIsEnabled)
                note.setData(Qt.UserRole + 2, hub)
                note.setSizeHint(QSize(0, 26))
                note.setToolTip("Click to list them again: " + ", ".join(hidden))
                self.collab.addItem(note)
                label = QLabel(f"  Show removed songs ({len(hidden)})")
                label.setObjectName("recentPath")
                self.collab.setItemWidget(note, label)
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

    def _refresh_join(self) -> None:
        """No shared folder yet: what joining takes, ticked off as it happens."""
        from sessionsync.core import drivelink

        while self.join_box.count():
            child = self.join_box.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        if self.settings.hubs():
            return
        title = QLabel("JOIN A COLLABORATOR")
        title.setObjectName("silkscreen")
        self.join_box.addWidget(title)
        installed = drivelink.drive_installed()
        signed = installed and drivelink.signed_in()
        found = drivelink.find_shared_folders() if signed else []
        steps = [
            (installed, "Google Drive for desktop is installed", "Download…", drivelink.DOWNLOAD_URL),
            (signed, "Signed in to Google Drive", None, None),
            (bool(found), "The shared folder is in your Drive (in the link they sent: "
             "Organize › Add shortcut › My Drive)", None, None),
        ]
        for done, text, action, url in steps:
            row = QHBoxLayout()
            label = QLabel(("✓  " if done else "○  ") + text)
            label.setWordWrap(True)
            label.setObjectName("recentName" if done else "recentPath")
            row.addWidget(label, 1)
            if not done and action:
                link = QPushButton(action)
                link.setFlat(True)
                link.setCursor(Qt.PointingHandCursor)
                link.setStyleSheet("font-size: 11px; padding: 0 4px; text-decoration: underline;")
                link.clicked.connect(lambda _=False, u=url: _open_url(u))
                row.addWidget(link)
            holder = QWidget()
            holder.setLayout(row)
            self.join_box.addWidget(holder)
        for folder in found:
            button = QPushButton(f"Connect “{folder.name}”")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, f=folder: self.connect_found.emit(f))
            self.join_box.addWidget(button)

    def _refresh_activity(self) -> None:
        """The newest few things collaborators did, by day, each with where to go."""
        from sessionsync.ui import activity

        while self.activity.count():
            child = self.activity.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        items = self.settings.activity()
        for day, lines in activity.grouped(items):
            label = QLabel(day)
            label.setObjectName("recentPath")
            self.activity.addWidget(label)
            for item in lines:
                self.activity.addWidget(_ActivityRow(item, self.activity_action.emit))
        shown = bool(items)
        self.activity_label.setVisible(shown)
        import sys

        self.alerts_button.setVisible(
            shown and sys.platform == "darwin" and not self.settings.flag("alerts_offered")
        )

    def _collab_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.collab.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        song = item.data(Qt.UserRole)
        hub = item.data(Qt.UserRole + 1)
        if song:
            hub, name = song
            local = self.settings.song_path(hub, name)
            menu.addAction(
                "Remove from Session Sync…",
                lambda: self.remove_requested.emit(hub, name, local if local.is_dir() else None),
            )
        elif hub is not None:
            menu.addAction("Remove This Shared Folder…", lambda: self.remove_hub_requested.emit(hub))
            if self.settings.hidden_songs(hub):
                menu.addAction("Show Removed Songs", lambda: self.unhide_requested.emit(hub))
        else:
            return
        menu.exec(self.collab.viewport().mapToGlobal(pos))

    def _recent_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.recent.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(Qt.UserRole))
        menu = QMenu(self)
        menu.addAction("Remove from Session Sync…", lambda: self.remove_requested.emit(None, path.name, path))
        menu.exec(self.recent.viewport().mapToGlobal(pos))

    def _song_activated(self, item: QListWidgetItem) -> None:
        hidden_of = item.data(Qt.UserRole + 2)
        if hidden_of is not None:
            self.unhide_requested.emit(hidden_of)
            return
        data = item.data(Qt.UserRole)
        if data:
            hub, song = data
            self.song_chosen.emit(hub, song)

    def _chosen(self, item: QListWidgetItem) -> None:
        self.project_chosen.emit(Path(item.data(Qt.UserRole)))


class _HubHeader(QWidget):
    def __init__(self, hub, on_share, on_invite=None):
        super().__init__()
        self.setFixedHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        name = QLabel(hub.name)
        name.setObjectName("recentName")
        row.addWidget(name, 1)
        share = QPushButton("Add a project…")
        share.setToolTip(f"Share a song from this computer with {hub.name}.")
        share.setCursor(Qt.PointingHandCursor)
        share.setFixedHeight(34)
        share.setStyleSheet("padding: 2px 14px; font-size: 11px;")
        share.setMinimumWidth(150)
        share.clicked.connect(lambda: on_share(hub))
        if on_invite is not None:
            invite = QPushButton("Invite…")
            invite.setToolTip(f"A message that tells someone how to join {hub.name}.")
            invite.setCursor(Qt.PointingHandCursor)
            invite.setFixedHeight(34)
            invite.setStyleSheet("padding: 2px 14px; font-size: 11px;")
            invite.clicked.connect(lambda: on_invite(hub))
            row.addWidget(invite)
        row.addWidget(share)
        if getattr(hub, "single_song", False):
            # Connected to one song, not the folder of songs: nothing to add to.
            share.hide()


class _SongRow(QWidget):
    """Song name, who did what last, and whether this computer is behind."""

    def __init__(self, row: "hubsync.SongRow", on_live=None):
        super().__init__()
        self.setFixedHeight(ROW_HEIGHT)
        self.setToolTip(
            "Click to see this song: where you stand, who is working, what changed."
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        outer.addWidget(holder, 1)
        column = QVBoxLayout(holder)
        column.setContentsMargins(14, 5, 4, 5)
        column.setSpacing(1)
        if on_live is not None:
            live = QPushButton("Open in Live")
            live.setCursor(Qt.PointingHandCursor)
            live.setFixedHeight(30)
            live.setStyleSheet("padding: 2px 12px; font-size: 11px;")
            live.setToolTip(
                "Opens YOUR copy of the song in Live (getting it first if you don't have one)."
            )
            live.clicked.connect(on_live)
            outer.addWidget(live)

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


class _ActivityRow(QWidget):
    """`16:02  Sam pushed v4 of Sketches — “bass idea” · Added Sax   Pull · See changes`"""

    def __init__(self, item, on_action):
        from sessionsync.ui import activity

        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(8)
        when = QLabel(activity.time_label(item.at))
        when.setObjectName("recentPath")
        when.setFixedWidth(40)
        row.addWidget(when)
        text = QLabel(item.text if len(item.text) <= 110 else item.text[:109] + "…")
        text.setTextFormat(Qt.PlainText)
        text.setToolTip(item.text)
        row.addWidget(text, 1)
        for label, action in item.links():
            link = QPushButton(label)
            link.setFlat(True)
            link.setCursor(Qt.PointingHandCursor)
            link.setStyleSheet("font-size: 11px; padding: 0 4px; text-decoration: underline;")
            link.clicked.connect(lambda _=False, a=action: on_action(item, a))
            row.addWidget(link)


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


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))


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
