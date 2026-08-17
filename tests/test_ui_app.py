"""Starting up: what the app makes of the arguments it is handed."""

from __future__ import annotations

from pathlib import Path

from sessionsync.ui.app import project_argument


def test_no_arguments_means_open_the_last_project():
    assert project_argument([]) is None


def test_a_folder_argument_is_the_project_to_open():
    assert project_argument(["/Users/me/Music/My Song"]) == Path("/Users/me/Music/My Song")


def test_finders_process_serial_number_is_not_a_project():
    """macOS hands a launched .app a -psn_0_12345 argument. It is not a path."""
    assert project_argument(["-psn_0_1234567"]) is None


def test_a_home_relative_path_is_expanded():
    assert project_argument(["~/Music/My Song"]) == Path.home() / "Music/My Song"


def test_other_flags_are_ignored_rather_than_opened_as_folders():
    assert project_argument(["--debug"]) is None
