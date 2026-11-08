"""One project pane: does it build, and does it draw a real repository?

Runs headless (offscreen). These are wiring tests — the wording and the button
rules are covered in `test_ui_presenter.py`, the tab shell in `test_ui_window.py`.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sessionsync.ui.engine import Engine  # noqa: E402
from sessionsync.ui.pane import ProjectPane  # noqa: E402
from tests.conftest import record


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def identity(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice")
    monkeypatch.setenv("SESSIONSYNC_EMAIL", "alice@example.com")


@pytest.fixture
def pane(qt_app, identity, alice):
    pane = ProjectPane(alice.project_root)
    yield pane
    pane.shutdown(end_session=False)


def snapshot_of(project_root):
    """What the engine would hand the window, without a thread in the way."""
    engine = Engine(project_root)
    captured = []
    engine.snapshot_ready.connect(captured.append)
    engine.refresh()
    return captured[-1]


def test_the_pane_opens_on_a_real_project(pane, alice):
    assert pane.project_name == "My Song"


def test_a_freshly_pushed_project_reads_as_up_to_date(pane, alice, identity):
    pane._show(snapshot_of(alice.project_root))

    assert pane.title_label.text() == "My Song"
    assert "Up to date" in pane.banner.text()
    assert pane.open_button.text() == "OPEN IN LIVE"
    assert pane.open_button.property("primary") is True
    assert "YOUR copy" in pane.open_button.toolTip()
    assert not pane.push_button.isEnabled()
    assert "Nothing has changed" in pane.push_button.toolTip()


def test_a_new_recording_appears_in_the_change_list(pane, alice, identity):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax take")

    pane._show(snapshot_of(alice.project_root))

    rows = [
        pane.changes.topLevelItem(i).text(1) for i in range(pane.changes.topLevelItemCount())
    ]
    assert "Samples/Recorded/Sax.aif" in rows
    assert pane.push_button.isEnabled()


def test_being_behind_makes_pull_the_thing_to_do(pane, alice, bob, identity):
    record(bob.project_root, "Samples/Recorded/Bass.aif", b"bass")
    bob.push(note="Bass")

    pane._show(snapshot_of(alice.project_root))

    assert "behind" in pane.banner.text()
    assert pane.pull_button.isEnabled()
    assert pane.pull_button.property("primary") is True
    assert pane.open_button.isEnabled()  # it asks to Pull first, rather than refusing


def test_changes_made_while_the_app_was_closed_are_mentioned_once(qt_app, identity, alice):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax take")
    first = snapshot_of(alice.project_root)
    assert "while Session Sync was closed" in first.late_note
    assert "nobody pushed" in first.late_note
    assert snapshot_of(alice.project_root).late_note == ""  # already said


def test_a_save_made_in_drive_shows_the_rescue_button(qt_app, identity, tmp_path):
    from sessionsync.adapters.ableton.adapter import AbletonAdapter
    from sessionsync.core.repository import Repository
    from sessionsync.storage.master_folder import DATA_DIR, MasterFolderStorage
    from tests.conftest import ALICE, als_marked, write_project

    shared = MasterFolderStorage(tmp_path / "drive" / "Song")
    repo = Repository.create(write_project(tmp_path / "me" / "Song", "Song"), shared,
                             adapter=AbletonAdapter(), author=ALICE)
    repo.push()
    (shared.root / DATA_DIR / "Song.als-shared").write_bytes(als_marked("saved in drive"))
    pane = ProjectPane(repo.project_root)

    pane._show(snapshot_of(repo.project_root))

    try:
        assert not pane.rescue_button.isHidden()
        assert not pane.push_button.isEnabled()
        assert "Google Drive" in pane.banner.text()
    finally:
        pane.shutdown(end_session=False)


def test_someone_else_working_is_shown_on_the_presence_line(pane, alice, bob, identity):
    bob.start_session()

    pane._show(snapshot_of(alice.project_root))

    assert "Bob" in pane.presence_label.text()
    assert pane.presence_label.isVisible() or pane.presence_label.text()


def test_a_background_check_does_not_take_the_buttons_away(pane, alice, identity):
    """A 6 GB project takes seconds to scan. Greying the app out every minute
    while it happens would make it feel broken."""
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax take")
    pane._show(snapshot_of(alice.project_root))

    pane._checking(True)

    assert pane.push_button.isEnabled()
    assert pane.status_line.text()


def test_a_real_operation_does_take_the_buttons_away(pane, alice, identity):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax take")
    pane._show(snapshot_of(alice.project_root))

    pane._busy_changed("Pushing…")

    assert not pane.push_button.isEnabled()


def test_the_recommended_action_stands_out(pane, alice, identity):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax take")

    pane._show(snapshot_of(alice.project_root))

    assert pane.push_button.property("primary") is True
    assert pane.pull_button.property("primary") is False


def test_the_engine_reports_failures_rather_than_raising(qt_app, identity, tmp_path):
    engine = Engine(tmp_path / "not a project")
    failures = []
    engine.operation_failed.connect(lambda name, exc: failures.append((name, exc)))

    engine.refresh()

    assert failures and failures[0][0] == "refresh"


def test_a_push_with_nothing_to_push_is_a_reported_failure_not_a_crash(qt_app, identity, alice):
    engine = Engine(alice.project_root)
    failures = []
    engine.operation_failed.connect(lambda name, exc: failures.append((name, exc)))

    engine.push("")

    assert failures[0][0] == "push"


def test_the_push_preview_says_what_consolidate_will_do(qt_app, identity, alice, tmp_path):
    from sessionsync.ui.dialogs import push_preview

    (alice.project_root / "Samples" / "Recorded" / "Old Take.wav").write_bytes(b"x" * 2048)
    outside = tmp_path / "Downloads" / "Room Mic.wav"
    outside.parent.mkdir()
    outside.write_bytes(b"room")
    from tests.test_consolidate import ref, set_with
    from tests.conftest import VOCAL

    (alice.project_root / "My Song.als").write_bytes(
        set_with(ref(VOCAL, "/x"), ref("Room Mic.wav", str(outside), ref_type="6"),
                 ref("Gone.wav", "/nowhere/Gone.wav"))
    )
    engine = Engine(alice.project_root)
    shots = []
    engine.snapshot_ready.connect(shots.append)
    engine.refresh()

    lines, problems = push_preview(shots[-1])
    text = "\n".join(lines)

    assert problems == []
    assert "Copying in 1 file from elsewhere" in text
    assert "Leaving 1 file the song does not use (2.0 KB)" in text
    assert "1 file the song uses cannot be found" in text and "Gone.wav" in text
    assert "Past Revisions" in text


def test_the_push_preview_blocks_an_unreadable_set(qt_app, identity, alice):
    from sessionsync.ui.dialogs import push_preview

    (alice.project_root / "My Song.als").write_bytes(b"broken")
    engine = Engine(alice.project_root)
    shots = []
    engine.snapshot_ready.connect(shots.append)
    engine.refresh()

    _, problems = push_preview(shots[-1])

    assert problems and "could not be read" in problems[0]


def test_a_failed_background_check_never_pops_up_a_dialog(pane, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    for _ in range(3):
        pane._operation_failed("refresh", PermissionError(13, "Permission denied", "Vocal.wav"))
    assert shown == []
    assert "Permission denied" in pane.status_line.text()


def test_a_push_live_still_holds_waits_and_goes_when_the_song_closes(pane, alice, identity, monkeypatch):
    """Windows: Live holds a new recording. No dialog; the push goes by itself later."""
    from dataclasses import replace
    from pathlib import Path

    from PySide6.QtWidgets import QMessageBox

    from sessionsync.core.errors import FileInUseError

    shown, pushed = [], []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    record(alice.project_root, "Samples/Recorded/Take 3.wav", b"take 3")
    snap = snapshot_of(alice.project_root)
    pane._show(replace(snap, live_open=True))
    pane.request_push.connect(pushed.append)

    pane._send_push("bass idea")
    pushed.clear()
    pane._operation_failed("push", FileInUseError(Path("Take 3.wav")))
    assert shown == []
    assert "pushes by itself" in pane.status_line.text()

    pane._show(replace(snap, live_open=True))
    assert pushed == []  # still open in Live
    pane._show(replace(snap, live_open=False))
    assert pushed == ["bass idea"]
    pane._show(replace(snap, live_open=False))
    assert pushed == ["bass idea"]  # once only
