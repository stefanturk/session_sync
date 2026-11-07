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
    from sessionsync.core.locations import free_folder

    monkeypatch.setattr(
        MainWindow, "_ask_where", lambda self, hub, song, found: free_folder(settings.local_root(), song)
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


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="the plain banner is the Mac's")
def test_the_notifier_uses_the_plain_banner_when_there_is_no_tray(qt_app, monkeypatch, no_real_notifications):
    monkeypatch.delenv("SESSIONSYNC_NO_NOTIFY", raising=False)
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    Notifier().show("hello", title="Sam")
    assert len(no_real_notifications) == 1  # went through the (stubbed) osascript path


# --- "▶ Open in Live" links in the shared folder ---------------------------------


def _repo_id(hub, song):
    return hub.storage_for(song).read_repo_metadata()["repository_id"]


def test_a_launcher_link_for_a_song_i_have_opens_my_copy_in_live(world, monkeypatch):
    from sessionsync.core.launcher import link_for
    from sessionsync.ui.pane import ProjectPane

    window, settings, hub = world
    window.open_song(hub, "Song B")
    window.close_tab(0)
    opened = []
    monkeypatch.setattr(ProjectPane, "open_in_live_when_ready", lambda self: opened.append(self.project_root))

    assert window.open_link(link_for(_repo_id(hub, "Song B"), "Song B"))

    assert opened == [settings.song_path(hub, "Song B")]


def test_a_launcher_link_for_a_song_i_do_not_have_gets_it_first(world, monkeypatch):
    from sessionsync.core.launcher import link_for
    from sessionsync.ui.pane import ProjectPane

    window, settings, hub = world
    opened = []
    monkeypatch.setattr(ProjectPane, "open_in_live_when_ready", lambda self: opened.append(self.project_root))

    window.open_link(link_for(_repo_id(hub, "Song B"), "Song B"))

    assert window.tabs.count() == 1
    assert opened and (opened[0] / "Song B.als").exists()
    assert opened[0].parent == settings.local_root()  # straight in the chosen folder


def test_a_launcher_link_for_an_unknown_shared_folder_asks_to_connect_it(world, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, _, _ = world
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)

    window.open_link("sessionsync://open?repo=nobody-knows&song=Mystery")

    assert shown and "Connect the shared folder" in shown[0]
    assert window.tabs.count() == 0


def test_a_moved_copy_is_noticed(world, monkeypatch):
    import shutil

    from PySide6.QtWidgets import QMessageBox
    from sessionsync.core.launcher import link_for

    window, settings, hub = world
    window.open_song(hub, "Song B")
    local = settings.song_path(hub, "Song B")
    window.close_tab(0)
    shutil.move(str(local), str(local.parent / "moved"))
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)

    window.open_link(link_for(_repo_id(hub, "Song B"), "Song B"))

    assert shown and "not at" in shown[0]


def test_links_that_are_not_ours_are_not_handled(world):
    window, _, _ = world
    assert window.open_link("https://example.com") is False
