"""Invite someone to a shared folder: a ready-to-send message, and the way to share it."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from sessionsync.core import drivelink


class InviteDialog(QDialog):
    def __init__(self, parent, hub, me: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Invite someone to {hub.name}")
        self.resize(640, 470)
        self.link = drivelink.folder_link(hub.path)
        layout = QVBoxLayout(self)

        step = QLabel(
            "<b>1.</b> Share the folder with them in Google Drive: open it, click "
            "<i>Share</i>, and add their Google account."
            if self.link
            else "<b>1.</b> Share the folder with them in Google Drive (right-click it in "
            "Drive on the web › Share › add their Google account)."
        )
        step.setWordWrap(True)
        layout.addWidget(step)
        layout.addWidget(QLabel("<b>2.</b> Send them this message (you can edit it):"))

        self.text = QTextEdit()
        self.text.setPlainText(drivelink.invite_text(hub.path.name, self.link, (me or {}).get("name", "")))
        self.text.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.copy_button = QPushButton("Copy Message")
        self.copy_button.clicked.connect(self.copy)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        if self.link:
            share = QPushButton("Open Folder in Drive")
            share.setToolTip("Opens the shared folder in your browser, where Share is.")
            share.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.link)))
            buttons.addButton(share, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        self.copy_button.setText("Copied ✓")
