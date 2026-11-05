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

COLLECT_TIP = (
    "Collect All and Save copies sounds that live elsewhere on your Mac into this "
    "project, so your friend's copy plays too. It does not remove sounds you no "
    "longer use; Live's Manage Files page has a separate clean-up for that."
)


def ask_put_back(parent, project_name: str, outside: list[str], missing: list[str]) -> str | None:
    """The checklist before putting your version back. Returns the note, or None.

    Both boxes must be ticked, unless the person chooses "Put back anyway".
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Put it back")
    dialog.setMinimumWidth(480)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    heading = QLabel(f"Ready to put {project_name} back in the shared folder?")
    heading.setWordWrap(True)
    layout.addWidget(heading)

    saved = QCheckBox("I saved the set in Live and closed it (or it is saved)")
    layout.addWidget(saved)

    collected = QCheckBox("I ran Collect All and Save in Live (File menu)")
    layout.addWidget(collected)
    if not outside and not missing:
        collected.setChecked(True)
        collected.setText("All sounds are inside the project already ✓")
    else:
        warn = QLabel(
            f"⚠  {len(outside)} sound(s) still live outside this project"
            + (f" and {len(missing)} can't be found" if missing else "")
            + ". Your friend would not hear them."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #E0A030;")
        layout.addWidget(warn)

    tip = QLabel(COLLECT_TIP)
    tip.setWordWrap(True)
    tip.setStyleSheet("font-size: 11px; opacity: 0.8;")
    layout.addWidget(tip)

    note = QLineEdit()
    note.setPlaceholderText("What did you do? (optional)")
    layout.addWidget(note)

    buttons = QDialogButtonBox()
    go = buttons.addButton("Put Back", QDialogButtonBox.AcceptRole)
    anyway = buttons.addButton("Put back anyway", QDialogButtonBox.ActionRole)
    buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
    go.setEnabled(False)
    layout.addWidget(buttons)

    def update() -> None:
        go.setEnabled(saved.isChecked() and collected.isChecked())

    saved.toggled.connect(update)
    collected.toggled.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    anyway.clicked.connect(dialog.accept)
    update()

    if dialog.exec() != QDialog.Accepted:
        return None
    return note.text().strip()
