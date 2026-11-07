"""The app remembers which projects you work on, so it opens where you left off."""

from __future__ import annotations

import pytest

from sessionsync.ui.settings import Settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "home"))
    return Settings()


def test_starts_with_nothing_remembered(settings):
    assert settings.recent_projects() == []
    assert settings.last_project() is None


def test_remembers_the_project_you_opened(settings, tmp_path):
    project = tmp_path / "My Song"
    project.mkdir()

    settings.remember(project)

    assert settings.last_project() == project


def test_survives_a_restart(settings, tmp_path):
    project = tmp_path / "My Song"
    project.mkdir()
    settings.remember(project)

    assert Settings().last_project() == project


def test_most_recent_project_comes_first(settings, tmp_path):
    first, second = tmp_path / "First", tmp_path / "Second"
    first.mkdir()
    second.mkdir()

    settings.remember(first)
    settings.remember(second)

    assert settings.recent_projects() == [second, first]


def test_reopening_a_project_moves_it_to_the_front_without_duplicating(settings, tmp_path):
    first, second = tmp_path / "First", tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    settings.remember(first)
    settings.remember(second)

    settings.remember(first)

    assert settings.recent_projects() == [first, second]


def test_a_project_folder_that_has_gone_is_not_offered(settings, tmp_path):
    project = tmp_path / "Deleted Song"
    project.mkdir()
    settings.remember(project)

    project.rmdir()

    assert settings.recent_projects() == []
    assert settings.last_project() is None


def test_a_corrupt_settings_file_does_not_stop_the_app_opening(settings):
    settings.path.parent.mkdir(parents=True, exist_ok=True)
    settings.path.write_text("{not json", encoding="utf-8")

    assert Settings().recent_projects() == []


def test_hubs_are_remembered_and_forgotten(settings, tmp_path):
    folder = tmp_path / "Sam Stefan Sketches"
    folder.mkdir()
    hub = settings.add_hub(folder, "Sam")
    settings.add_hub(folder, "Sam")  # connecting twice does not duplicate
    assert [(h.name, h.path) for h in settings.hubs()] == [("Sam", folder)]

    assert settings.seen(hub) is None
    settings.set_seen(hub, {"Song A": 3})
    assert settings.seen(hub) == {"Song A": 3}

    settings.remove_hub(hub)
    assert settings.hubs() == [] and settings.seen(hub) is None


def test_new_songs_go_on_the_desktop_unless_icloud_syncs_it(settings, tmp_path, no_real_desktop):
    assert settings.local_root() == no_real_desktop / "Desktop"
    (no_real_desktop / "Library/Mobile Documents/com~apple~CloudDocs/Desktop").mkdir(parents=True)
    assert settings.local_root() == no_real_desktop / "Music" / "Session Sync"
    settings.set_local_root(tmp_path / "mine")
    assert settings.local_root() == tmp_path / "mine"
