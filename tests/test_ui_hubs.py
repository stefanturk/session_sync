"""Shared folders in the window: listing, opening a song, sharing, and banner clicks."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sessionsync.ui import hubsync  # noqa: E402
from sessionsync.ui.notify import Notifier  # noqa: E402
from sessionsync.ui.settings import Settings  # noqa: E402
from sessionsync.ui.window import MainWindow  # noqa: E402
from tests.conftest import ALICE, BOB, write_project  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def world(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice")
    monkeypatch.setenv("SESSIONSYNC_EMAIL", "alice@example.com")
    settings = Settings()
    settings.set_local_root(tmp_path / "music")
    folder = tmp_path / "Drive" / "Sam Stefan Sketches"
    folder.mkdir(parents=True)
    hub = settings.add_hub(folder, "Sam")
    hub.add_song("Song B", write_project(tmp_path / "sam" / "Song B", "Song B"), BOB)
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "music" / "Sam"))
    )
    window = MainWindow(settings)
    yield window, settings, hub
    window.close_everything()


def test_start_screen_lists_the_hub_and_its_songs(world):
    window, _, _ = world
    window.start.refresh()
    assert window.start.collab.count() == 2  # hub header + Song B
    assert window.start.collab.isHidden() is False


def test_hubs_with_no_songs_say_so(world, tmp_path):
    window, settings, _ = world
    empty = tmp_path / "Drive" / "Noah Stefan Sketches"
    empty.mkdir()
    settings.add_hub(empty, "Noah")
    window.start.refresh()
    assert window.start.collab.count() == 2 + 2  # Sam header + Song B, Noah header + note


def test_opening_a_new_song_downloads_it_and_gives_it_a_tab(world):
    window, settings, hub = world
    window.open_song(hub, "Song B")

    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == "Song B"
    assert settings.song_path(hub, "Song B").is_dir()
    assert hubsync.song_rows(hub, settings)[0].state == "current"


def test_clicking_a_banner_opens_that_song(world):
    window, settings, hub = world
    window.open_song(hub, "Song B")
    window.close_tab(0)
    assert window.tabs.count() == 0

    window.show_news_target((hub, "Song B", settings.song_path(hub, "Song B")))
    assert window.tabs.count() == 1


def test_clicking_a_banner_for_a_song_not_here_yet_shows_the_list(world):
    window, _, hub = world
    window.show_news_target((hub, "Song B", None))
    assert window.stack.currentWidget() is window.start


def test_a_project_banner_click_focuses_its_tab(world, tmp_path):
    window, settings, hub = world
    window.open_song(hub, "Song B")
    pane = window.panes()[0]
    window.show_news_target(pane.project_root)
    assert window.tabs.currentWidget() is pane


def test_the_notifier_reports_the_target_that_was_clicked(qt_app):
    got = []
    notifier = Notifier(on_click=got.append)
    notifier.disabled = True
    notifier._target = "song-a"
    notifier._clicked()
    assert got == ["song-a"]


def test_the_notifier_uses_the_plain_banner_when_there_is_no_tray(qt_app, monkeypatch, no_real_notifications):
    monkeypatch.delenv("SESSIONSYNC_NO_NOTIFY", raising=False)
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    Notifier().show("hello", title="Sam")
    assert len(no_real_notifications) == 1  # went through the (stubbed) osascript path
