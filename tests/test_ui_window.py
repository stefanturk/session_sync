"""The window itself: does it build, and does it draw a real repository?

Runs headless (offscreen). These are wiring tests — the wording and the button
rules are covered in `test_ui_presenter.py`.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sessionsync.ui.engine import Engine  # noqa: E402
from sessionsync.ui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def identity(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice")
    monkeypatch.setenv("SESSIONSYNC_EMAIL", "alice@example.com")


@pytest.fixture
def window(qt_app, identity, alice):
    window = MainWindow(alice.project_root)
    yield window
    window.close()


def snapshot_of(project_root):
    """What the engine would hand the window, without a thread in the way."""
    engine = Engine(project_root)
    captured = []
    engine.snapshot_ready.connect(captured.append)
    engine.refresh()
    return captured[-1]


def test_the_window_opens_on_a_real_project(window, alice):
    assert window.windowTitle() == "SessionSync — My Song"


def test_a_freshly_pushed_project_reads_as_up_to_date(window, alice, identity):
    window._show(snapshot_of(alice.project_root))

    assert window.title_label.text() == "My Song"
    assert "Up to date" in window.banner.text()
    assert window.session_button.text() == "Start Session"
    assert not window.push_button.isEnabled()


def test_a_new_recording_appears_in_the_change_list(window, alice, identity):
    (alice.project_root / "Samples" / "Recorded" / "Sax.aif").write_bytes(b"sax take")

    window._show(snapshot_of(alice.project_root))

    rows = [
        window.changes.topLevelItem(i).text(1) for i in range(window.changes.topLevelItemCount())
    ]
    assert "Samples/Recorded/Sax.aif" in rows
    assert window.push_button.isEnabled()


def test_being_behind_disables_starting_a_session(window, alice, bob, identity):
    (bob.project_root / "Samples" / "Recorded" / "Bass.aif").write_bytes(b"bass")
    bob.push(note="Bass")

    window._show(snapshot_of(alice.project_root))

    assert "behind" in window.banner.text()
    assert not window.session_button.isEnabled()
    assert window.pull_button.isEnabled()
    assert "Pull latest" in window.session_button.toolTip()


def test_someone_else_working_is_shown_on_the_presence_line(window, alice, bob, identity):
    bob.start_session()

    window._show(snapshot_of(alice.project_root))

    assert "Bob" in window.presence_label.text()
    assert window.presence_label.isVisible() or window.presence_label.text()


def test_a_background_check_does_not_take_the_buttons_away(window, alice, identity):
    """A 6 GB project takes seconds to scan. Greying the app out every minute
    while it happens would make it feel broken."""
    (alice.project_root / "Samples" / "Recorded" / "Sax.aif").write_bytes(b"sax take")
    window._show(snapshot_of(alice.project_root))

    window._checking(True)

    assert window.push_button.isEnabled()
    assert window.statusBar().currentMessage()


def test_a_real_operation_does_take_the_buttons_away(window, alice, identity):
    (alice.project_root / "Samples" / "Recorded" / "Sax.aif").write_bytes(b"sax take")
    window._show(snapshot_of(alice.project_root))

    window._busy_changed("Pushing…")

    assert not window.push_button.isEnabled()


def test_the_recommended_action_stands_out(window, alice, identity):
    (alice.project_root / "Samples" / "Recorded" / "Sax.aif").write_bytes(b"sax take")

    window._show(snapshot_of(alice.project_root))

    assert window.push_button.property("primary") is True
    assert window.pull_button.property("primary") is False


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
