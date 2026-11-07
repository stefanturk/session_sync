"""The tab shell: several projects at once, and what closing one asks.

Dialogs are never run — `_confirm_close` is substituted, so these tests say what
the shell *does* with each answer rather than driving a modal.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sessionsync.ui.settings import Settings  # noqa: E402
from sessionsync.ui.window import MainWindow  # noqa: E402
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
def window(qt_app, identity):
    window = MainWindow(Settings())
    yield window
    window.close_everything()


# --- opening -------------------------------------------------------------


def test_it_opens_on_the_start_screen_not_a_project(window):
    assert window.tabs.count() == 0
    assert window.stack.currentWidget() is window.start


def test_opening_a_project_gives_it_a_tab(window, alice):
    window.open_project(alice.project_root)

    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == "My Song"
    assert window.stack.currentWidget() is window.tabs


def test_two_projects_are_two_tabs(window, alice, bob):
    window.open_project(alice.project_root)
    window.open_project(bob.project_root)

    assert window.tabs.count() == 2


def test_opening_the_same_project_twice_focuses_the_tab_it_is_already_in(window, alice, bob):
    """Two Repositories on one state.db from two threads could interleave
    their writes, so the same folder must never get a second engine."""
    first = window.open_project(alice.project_root)
    window.open_project(bob.project_root)

    again = window.open_project(alice.project_root)

    assert window.tabs.count() == 2
    assert again is first
    assert window.tabs.currentWidget() is first


def test_a_project_opened_by_a_different_route_to_the_same_folder_is_not_reopened(
    window, alice
):
    window.open_project(alice.project_root)

    window.open_project(alice.project_root / "Samples" / ".." )

    assert window.tabs.count() == 1


def test_opening_a_project_remembers_it_for_next_time(window, alice):
    window.open_project(alice.project_root)

    assert alice.project_root in Settings().recent_projects()


def test_a_folder_that_is_not_a_project_is_refused_and_explained(window, tmp_path):
    refused = []
    window._not_a_project = refused.append

    window.open_project(tmp_path / "just some folder")

    assert window.tabs.count() == 0
    assert refused, "silently doing nothing would look like the app is broken"


# --- the tab itself ------------------------------------------------------


def test_the_tab_carries_a_light_showing_how_that_project_stands(window, alice):
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))

    assert not window.tabs.tabIcon(0).isNull()
    assert "UP TO DATE" in window.tabs.tabToolTip(0)


def test_the_window_title_follows_the_open_project(window, alice):
    window.open_project(alice.project_root)

    assert "My Song" in window.windowTitle()


def test_the_title_goes_back_to_the_app_name_with_nothing_open(window, alice):
    window.open_project(alice.project_root)
    window.close_tab(0)

    assert window.windowTitle() == "SessionSync"


def test_only_the_visible_tab_checks_often(window, alice, bob):
    from sessionsync.ui.pane import ACTIVE_REFRESH_MS, BACKGROUND_REFRESH_MS

    first = window.open_project(alice.project_root)
    second = window.open_project(bob.project_root)

    assert second.timer.interval() == ACTIVE_REFRESH_MS
    assert first.timer.interval() == BACKGROUND_REFRESH_MS


def test_a_background_tab_still_beats_at_the_normal_rate(window, alice, bob):
    """A late heartbeat makes other people think you stopped working."""
    from sessionsync.core.session import HEARTBEAT_SECONDS

    first = window.open_project(alice.project_root)
    window.open_project(bob.project_root)

    assert first.heartbeat_timer.interval() == HEARTBEAT_SECONDS * 1000


# --- closing -------------------------------------------------------------


def test_closing_a_project_with_nothing_outstanding_does_not_ask(window, alice):
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))
    asked = _never_ask(window)

    window.close_tab(0)

    assert asked == []
    assert window.tabs.count() == 0


def test_closing_a_project_with_unpushed_work_asks_first(window, alice):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))
    asked = _answer(window, "cancel")

    window.close_tab(0)

    assert asked == [pane]
    assert window.tabs.count() == 1, "cancelling must leave the project open"


def test_closing_anyway_closes_it(window, alice):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))
    _answer(window, "close")

    window.close_tab(0)

    assert window.tabs.count() == 0
    assert (alice.project_root / "Samples" / "Recorded" / "Sax.aif").exists(), (
        "closing must never remove the recording"
    )


def test_choosing_to_push_first_keeps_the_project_open(window, alice, monkeypatch):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))
    _answer(window, "push")
    pushed = []
    monkeypatch.setattr(pane, "push", lambda: pushed.append(True))

    window.close_tab(0)

    assert window.tabs.count() == 1
    assert pushed == [True]


def test_closing_a_tab_with_live_closed_and_nothing_to_push_leaves_no_ghost(window, alice, bob):
    """A lingering "working" has collaborators believing you are still
    recording for up to fifteen minutes."""
    alice.refresh_presence(live_here=True)  # Live had it open earlier
    assert bob.other_sessions()
    window.open_project(alice.project_root)
    _never_ask(window)

    window.close_tab(0)

    assert bob.other_sessions() == []


def test_closing_a_tab_keeps_unpushed_work_visible_to_the_others(window, alice, bob):
    """Closing ends nothing: the others still see that you have work to push."""
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    pane._show(_snapshot_of(alice.project_root))
    _answer(window, "close")

    window.close_tab(0)

    assert [s["state"] for s in bob.other_sessions()] == ["unpushed"]


def test_quitting_with_unpushed_work_asks_once_for_everything(window, alice, bob):
    for repo in (alice, bob):
        record(repo.project_root, "Samples/Recorded/Sax.aif", b"sax")
    for repo in (alice, bob):
        pane = window.open_project(repo.project_root)
        pane._show(_snapshot_of(repo.project_root))
    asked = _answer_quit(window, False)

    window.close()

    assert len(asked) == 1, "one dialog for the app, not one per tab"
    assert window.tabs.count() == 2, "declining must leave everything open"


# --- helpers -------------------------------------------------------------


def _spin_until(predicate, timeout_ms: int = 3000) -> bool:
    """Let the engine thread get on with it, without an event loop to run in."""
    from PySide6.QtCore import QThread

    for _ in range(timeout_ms // 20):
        QApplication.processEvents()
        if predicate():
            return True
        QThread.msleep(20)
    return False


def _snapshot_of(project_root):
    from sessionsync.ui.engine import Engine

    engine = Engine(project_root)
    captured = []
    engine.snapshot_ready.connect(captured.append)
    engine.refresh()
    return captured[-1]


def _answer(window, answer):
    asked = []

    def fake(pane):
        asked.append(pane)
        return answer

    window._confirm_close = fake
    return asked


def _never_ask(window):
    asked = []

    def fake(pane):
        asked.append(pane)
        return "close"

    window._confirm_close = fake
    return asked


def _answer_quit(window, allow):
    asked = []

    def fake(panes):
        asked.append(panes)
        return allow

    window._confirm_quit = fake
    return asked


# --- closing the song in Live ------------------------------------------------


def _asked_to_push(monkeypatch):
    from sessionsync.ui import pane as pane_module

    asked = []

    def fake(parent, snapshot, *, live_closed=False):
        asked.append(live_closed)
        return None  # "Not Now"

    monkeypatch.setattr(pane_module, "ask_push", fake)
    return asked


def test_closing_the_song_in_live_offers_to_push(window, alice, monkeypatch):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)

    window.live_changed(str(alice.project_root / "My Song.als"), "")  # Live quit

    assert _spin_until(lambda: asked)
    assert asked == [True]
    assert window.tabs.currentWidget() is pane


def test_saying_not_now_leaves_the_work_where_it_is(window, alice, monkeypatch):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)

    window.live_changed(str(alice.project_root / "My Song.als"), "")

    assert _spin_until(lambda: asked)
    assert (alice.project_root / "Samples" / "Recorded" / "Sax.aif").exists()
    assert alice.remote_version() == 1, "Not Now pushes nothing"


def test_closing_live_with_nothing_changed_asks_nothing(window, alice, monkeypatch):
    pane = window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)

    window.live_changed(str(alice.project_root / "My Song.als"), "")
    _spin_until(lambda: pane.snapshot is not None and not pane._ask_after_live_closed)
    _spin_until(lambda: False, 300)

    assert asked == []


def test_switching_live_to_another_song_counts_as_closing_this_one(window, alice, monkeypatch, tmp_path):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)

    window.live_changed(str(alice.project_root / "My Song.als"), str(tmp_path / "Other" / "Other.als"))

    assert _spin_until(lambda: asked)


def test_opening_another_song_in_live_does_not_ask_about_this_one(window, alice, monkeypatch, tmp_path):
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)

    window.live_changed("", str(tmp_path / "Other" / "Other.als"))
    _spin_until(lambda: False, 500)

    assert asked == [] and pane._ask_after_live_closed is False


def test_if_someone_pushed_meanwhile_it_offers_catch_up_not_push(window, alice, bob, monkeypatch):
    from tests.conftest import als_marked

    (bob.project_root / "My Song.als").write_bytes(als_marked(b"bob's take"))
    bob.push(note="Bob")
    record(alice.project_root, "Samples/Recorded/Sax.aif", b"sax")
    pane = window.open_project(alice.project_root)
    asked = _asked_to_push(monkeypatch)
    offered = []
    monkeypatch.setattr(pane, "_confirm_catch_up_after_close", lambda: offered.append(True) or False)

    window.live_changed(str(alice.project_root / "My Song.als"), "")

    assert _spin_until(lambda: offered)
    assert asked == [], "pushing is impossible until you catch up, so don't offer it"


def test_the_live_watcher_reports_each_change_once():
    from sessionsync.ui.livewatcher import LiveWatcher

    watcher = LiveWatcher()
    changes = []
    watcher.changed.connect(lambda before, now: changes.append((before, now)))
    for seen in ("/a/Song.als", "/a/Song.als", "", "", "/b/Other.als"):
        watcher._seen(seen)

    assert changes == [("", "/a/Song.als"), ("/a/Song.als", ""), ("", "/b/Other.als")]
