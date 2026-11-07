"""Small questions the app asks in plain words."""

from __future__ import annotations

from sessionsync.core.summary import describe_catch_up  # noqa: F401  (re-exported for the panes)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

def push_preview(snapshot) -> tuple[list[str], list[str]]:
    """(lines to show, reasons Push cannot go) for the Push dialog.

    Push always consolidates first: only what the Live Sets use is sent, sounds
    from elsewhere on this computer are copied in, and unused audio stays here.
    """
    from sessionsync.ui.presenter import human_size

    status = snapshot.status
    lines = [
        f"Sending {human_size(status.upload_bytes)} new "
        f"(the song is {human_size(status.project_bytes)} in all)."
    ]
    if snapshot.media_outside:
        lines.append(
            f"Copying in {_files(len(snapshot.media_outside))} from elsewhere on this computer "
            "(into Samples/Imported), so everyone hears them."
        )
    if snapshot.unused:
        lines.append(
            f"Leaving {_files(len(snapshot.unused))} the song does not use "
            f"({human_size(snapshot.unused_bytes)}) on this computer only."
        )
    if snapshot.media_missing:
        lines.append(
            f"⚠  {_files(len(snapshot.media_missing))} the song uses cannot be found on this "
            "Mac, so nobody will hear them: " + ", ".join(snapshot.media_missing[:3])
            + ("…" if len(snapshot.media_missing) > 3 else "")
        )
    if snapshot.packs:
        lines.append(f"Collaborators need these Ableton Packs installed: {', '.join(snapshot.packs)}.")
    lines.append("The main Live Set is also saved into Past Revisions.")
    return lines, list(snapshot.problems)


def _files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def ask_push(parent, snapshot) -> str | None:
    """What Push is about to do, then a note. Returns the note, or None."""
    lines, problems = push_preview(snapshot)
    dialog = QDialog(parent)
    dialog.setWindowTitle("Push")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    heading = QLabel(f"Push {snapshot.project_name} to the shared folder?")
    heading.setWordWrap(True)
    layout.addWidget(heading)

    for line in lines:
        label = QLabel(line)
        label.setWordWrap(True)
        if line.startswith("⚠"):
            label.setStyleSheet("color: #E0A030;")
        layout.addWidget(label)

    for problem in problems:
        label = QLabel("✕  " + problem)
        label.setWordWrap(True)
        label.setStyleSheet("color: #E05050;")
        layout.addWidget(label)

    saved = QCheckBox("I saved the set in Live")
    layout.addWidget(saved)

    note = QLineEdit()
    note.setPlaceholderText("What did you do? (optional — it names the Past Revision)")
    layout.addWidget(note)

    buttons = QDialogButtonBox()
    go = buttons.addButton("Push", QDialogButtonBox.AcceptRole)
    buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
    layout.addWidget(buttons)

    def update() -> None:
        go.setEnabled(saved.isChecked() and not problems)
        go.setToolTip("Fix the problem above first." if problems else "")

    saved.toggled.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    update()

    if dialog.exec() != QDialog.Accepted:
        return None
    return note.text().strip()
