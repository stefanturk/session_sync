"""The History window: every version, what changed in it, and ways back."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from sessionsync.ui.presenter import human_size, local_time


def who(entry) -> str:
    return (entry.author or {}).get("name") or "Someone"


def list_line(entry, latest: int) -> str:
    """`v3 · Sam · 07 Nov 2026, 4:02 PM` plus its summary underneath."""
    head = f"v{entry.version} · {who(entry)} · {local_time(entry.created_at)}"
    if entry.version == latest:
        head += "  (latest)"
    body = entry.summary or "no summary"
    if entry.note:
        body = f"“{entry.note}” · {body}"
    return f"{head}\n{body}"


def details_html(entry) -> str:
    """What one version changed, file by file."""
    from html import escape

    parts = [
        f"<h3 style='margin:0'>v{entry.version} — {escape(who(entry))}</h3>",
        f"<p style='margin:2px 0 8px 0;color:#999'>{escape(local_time(entry.created_at))}</p>",
    ]
    if entry.note:
        parts.append(f"<p>“{escape(entry.note)}”</p>")
    if entry.summary:
        parts.append(f"<p><b>{escape(entry.summary)}</b></p>")
    for title, paths in (("Added", entry.added), ("Changed", entry.modified), ("Removed", entry.deleted)):
        if not paths:
            continue
        rows = []
        for path in paths[:200]:
            size = entry.sizes.get(path) if entry.sizes else None
            rows.append(
                f"<li>{escape(path)}"
                + (f" <span style='color:#999'>{human_size(size)}</span>" if size else "")
                + "</li>"
            )
        if len(paths) > 200:
            rows.append(f"<li>…and {len(paths) - 200} more</li>")
        parts.append(f"<p style='margin-bottom:0'>{title} ({len(paths)})</p><ul style='margin-top:2px'>{''.join(rows)}</ul>")
    if not entry.restorable:
        parts.append("<p style='color:#E0A030'>This version is older than the versions that are kept.</p>")
    return "".join(parts)


class HistoryDialog(QDialog):
    """Pick a version; Open it in Live, Restore a copy, or make it the latest again.

    `chosen` is (action, version) after the dialog closes, action being "open",
    "restore" or "latest"; None when it was just closed.
    """

    def __init__(self, parent, entries, project_name: str = ""):
        super().__init__(parent)
        self.entries = list(entries)
        self.chosen: tuple[str, int] | None = None
        self.setWindowTitle(f"History — {project_name}" if project_name else "History")
        self.resize(820, 520)
        latest = max((e.version for e in self.entries), default=0)
        self.latest = latest

        layout = QVBoxLayout(self)
        if not self.entries:
            layout.addWidget(QLabel("No versions yet."))
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        self.list = QListWidget()
        self.list.setMinimumWidth(330)
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        for entry in self.entries:
            item = QListWidgetItem(list_line(entry, latest))
            item.setData(Qt.UserRole, entry.version)
            self.list.addItem(item)
        row.addWidget(self.list, 2)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        row.addWidget(self.details, 3)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.open_button = QPushButton("Open in Live")
        self.open_button.setToolTip("Open this version's Live Set from Past Revisions; your copy is not touched.")
        self.restore_button = QPushButton("Restore a Copy…")
        self.restore_button.setToolTip("Build this whole version in a new folder beside your song.")
        self.latest_button = QPushButton("Make This the Latest Again…")
        self.latest_button.setToolTip(
            "Undo the pushes after it: shares a new version with exactly these files. "
            "Nothing is lost — the later versions stay in History."
        )
        for b in (self.open_button, self.restore_button, self.latest_button):
            buttons.addButton(b, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.open_button.clicked.connect(lambda: self._choose("open"))
        self.restore_button.clicked.connect(lambda: self._choose("restore"))
        self.latest_button.clicked.connect(self._make_latest)
        self.list.currentRowChanged.connect(self._show)
        if self.entries:
            self.list.setCurrentRow(0)
        else:
            self._show(-1)

    def current(self):
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _show(self, row: int) -> None:
        entry = self.entries[row] if 0 <= row < len(self.entries) else None
        self.details.setHtml(details_html(entry) if entry else "")
        usable = bool(entry and entry.restorable)
        self.open_button.setEnabled(usable and bool(entry.revision_path))
        self.restore_button.setEnabled(usable)
        self.latest_button.setEnabled(usable and entry.version != self.latest)

    def _choose(self, action: str) -> None:
        entry = self.current()
        if entry is None:
            return
        self.chosen = (action, entry.version)
        self.accept()

    def _make_latest(self) -> None:
        entry = self.current()
        if entry is None:
            return
        later = [e for e in self.entries if e.version > entry.version]
        names = sorted({who(e) for e in later})
        answer = QMessageBox.question(
            self,
            f"Make v{entry.version} the latest again?",
            f"Everyone gets the song as it was in v{entry.version} the next time they pull.\n\n"
            f"The {len(later)} version{'s' if len(later) != 1 else ''} after it "
            f"(by {', '.join(names)}) stay in History, so this can be undone the same way.\n\n"
            "Your own copy is updated to it now; if you have changes that are not pushed, "
            "Session Sync asks first.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Ok:
            self._choose("latest")
