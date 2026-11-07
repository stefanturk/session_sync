"""Settings: where new songs go, and where each of your songs is."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from sessionsync.core import locations
from sessionsync.ui.settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(620, 400)
        column = QVBoxLayout(self)

        heading = QLabel("PUT NEW SONGS IN")
        heading.setObjectName("silkscreen")
        column.addWidget(heading)
        row = QHBoxLayout()
        self.root_label = QLabel()
        self.root_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.root_label, 1)
        change = QPushButton("Change…")
        change.setToolTip(
            "Where your own copy of a song goes when you get one. Must be on this computer — "
            "not in Google Drive, Dropbox, OneDrive or iCloud Drive."
        )
        change.clicked.connect(self._change_root)
        row.addWidget(change)
        column.addLayout(row)
        note = QLabel("Songs you already have stay where they are.")
        note.setObjectName("summary")
        column.addWidget(note)
        column.addSpacing(12)

        heading = QLabel("YOUR SONGS")
        heading.setObjectName("silkscreen")
        column.addWidget(heading)
        self.songs = QTreeWidget()
        self.songs.setHeaderLabels(["SONG", "ON THIS COMPUTER"])
        self.songs.setRootIsDecorated(False)
        self.songs.setColumnWidth(0, 200)
        self.songs.itemDoubleClicked.connect(self._reveal)
        self.songs.setToolTip("Double-click a song to show it in Finder.")
        column.addWidget(self.songs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        reveal = buttons.addButton("Show in Finder", QDialogButtonBox.ActionRole)
        reveal.clicked.connect(lambda: self._reveal(self.songs.currentItem()))
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        root = self.settings.local_root()
        self.root_label.setText(_pretty(root))
        self.songs.clear()
        for key, path in sorted(self.settings.songs(), key=lambda kv: kv[0].split("|")[-1].lower()):
            song = key.split("|")[-1]
            where = _pretty(path) if path.exists() else f"{_pretty(path)}  (not there any more)"
            item = QTreeWidgetItem([song, where])
            item.setData(0, Qt.UserRole, str(path))
            item.setToolTip(1, str(path))
            self.songs.addTopLevelItem(item)

    def _change_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Put new songs in…", str(self.settings.local_root())
        )
        if not chosen:
            return
        verdict = locations.check_location(
            Path(chosen),
            hubs=[h.path for h in self.settings.hubs()],
            songs=[p for _, p in self.settings.songs()],
        )
        if not verdict.ok:
            QMessageBox.warning(self, "Not that folder", verdict.reason)
            return
        if verdict.warning:
            QMessageBox.information(self, "Worth knowing", verdict.warning)
        self.settings.set_local_root(Path(chosen))
        self._fill()

    @staticmethod
    def _reveal(item) -> None:
        if item is None:
            return
        path = Path(item.data(0, Qt.UserRole))
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _pretty(path: Path) -> str:
    try:
        return f"~/{Path(path).relative_to(locations.home())}"
    except ValueError:
        return str(path)
