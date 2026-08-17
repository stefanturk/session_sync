import pytest

from sessionsync.core.localstate import LocalState


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "My Song"
    root.mkdir()
    return root


def test_creates_its_working_folder_inside_the_project(project):
    LocalState.create(project, repository_id="r1", remote="/remote/My Song")

    assert (project / ".sessionsync" / "state.db").exists()


def test_remembers_the_repository_it_belongs_to_across_restarts(project):
    LocalState.create(project, repository_id="r1", remote="/remote/My Song")

    reopened = LocalState.open(project)

    assert reopened.repository_id == "r1"
    assert reopened.remote == "/remote/My Song"


def test_a_new_working_copy_has_no_version_yet(project):
    state = LocalState.create(project, repository_id="r1", remote="/remote")

    assert state.base_version == 0


def test_base_version_persists(project):
    LocalState.create(project, repository_id="r1", remote="/remote").base_version = 7

    assert LocalState.open(project).base_version == 7


def test_opening_a_folder_that_was_never_set_up_raises(project):
    with pytest.raises(FileNotFoundError):
        LocalState.open(project)


def test_stores_and_clears_the_session_baseline(project):
    state = LocalState.create(project, repository_id="r1", remote="/remote")
    state.baseline = {"Song.als": {"sha256": "ab", "size": 3}}

    assert LocalState.open(project).baseline == {"Song.als": {"sha256": "ab", "size": 3}}

    state.baseline = None
    assert LocalState.open(project).baseline is None


def test_the_working_folder_is_hidden_from_the_synced_project(project):
    state = LocalState.create(project, repository_id="r1", remote="/remote")

    assert state.dir.name == ".sessionsync"
    assert state.trash_dir.parent == state.dir
