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
