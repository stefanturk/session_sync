"""A song's own folders are never songs, and nothing deletes a shared song's history.

What happened on 7 Nov: a collaborator connected the *song* folder as the shared
folder. The app then set up the song's data folder and Live's Backup folder as
songs of their own, and "Add a project" on the song folder itself failed and
cleaned up by deleting the song's .sessionsync — its whole history.
"""

import pytest

from sessionsync.core import master
from sessionsync.core.errors import PartOfASongError, SharedHistoryMissingError
from sessionsync.core.hub import Hub
from sessionsync.core.repository import Repository
from sessionsync.storage.master_folder import DATA_DIR, META_DIR, MasterFolderStorage

from tests.conftest import ALICE, BOB, write_project


@pytest.fixture
def hub_dir(tmp_path):
    return tmp_path / "drive" / "Sam Stefan Sketches"


@pytest.fixture
def song(tmp_path, hub_dir):
    """A song shared the normal way: hub/My Song with the data folder inside."""
    local = write_project(tmp_path / "alice" / "My Song")
    hub_dir.mkdir(parents=True)
    master.add_local_project(local, hub_dir, author=ALICE)
    return hub_dir / "My Song"


def _live_backup_in(song):
    """Live's Backup folder, as Live makes one beside a set it saved."""
    backup = song / "Backup"
    write_project(backup, "My Song [2026-11-04 174806]")
    return backup


# --- where the song is ------------------------------------------------------


def test_song_around_finds_the_song_from_anywhere_inside_it(song):
    assert MasterFolderStorage.song_around(song) == song
    assert MasterFolderStorage.song_around(song / DATA_DIR) == song
    assert MasterFolderStorage.song_around(song / DATA_DIR / "Samples" / "Recorded") == song
    assert MasterFolderStorage.song_around(song.parent) is None


def test_song_around_still_knows_the_song_when_its_history_is_gone(song):
    import shutil

    shutil.rmtree(song / META_DIR)
    assert MasterFolderStorage.song_around(song / DATA_DIR) == song
    assert MasterFolderStorage.song_around(song) == song


def test_a_working_copy_is_not_a_shared_song(tmp_path, song):
    assert MasterFolderStorage.song_around(tmp_path / "alice" / "My Song") is None


# --- never a song inside a song ---------------------------------------------


def test_the_data_folder_cannot_be_set_up_as_a_song(song):
    before = sorted(p.relative_to(song) for p in song.rglob("*"))
    with pytest.raises(PartOfASongError):
        master.adopt(song / DATA_DIR, author=BOB)
    assert sorted(p.relative_to(song) for p in song.rglob("*")) == before


def test_lives_backup_folder_cannot_be_set_up_as_a_song(song):
    backup = _live_backup_in(song)
    with pytest.raises(PartOfASongError):
        master.adopt(backup, author=BOB)
    assert not (backup / META_DIR).exists()


def test_a_folder_of_songs_cannot_be_set_up_as_a_song(song, hub_dir):
    write_project(hub_dir, "Loose")  # an .als dropped straight into the shared folder
    with pytest.raises(PartOfASongError):
        master.adopt(hub_dir, author=BOB)
    assert not (hub_dir / META_DIR).exists()


# --- "Add a project" never touches a shared song ------------------------------


def test_adding_the_shared_song_folder_itself_is_refused_and_keeps_its_history(song):
    history = sorted(p.relative_to(song) for p in (song / META_DIR).rglob("*"))
    with pytest.raises(PartOfASongError):
        master.add_local_project(song, song, author=BOB)
    with pytest.raises(PartOfASongError):
        master.add_local_project(song, song.parent, author=BOB)
    assert sorted(p.relative_to(song) for p in (song / META_DIR).rglob("*")) == history
    assert not (song / META_DIR / "state.db").exists()
    assert not (song / "My Song").exists()


def test_adding_a_project_from_inside_the_shared_folder_is_refused(hub_dir, song):
    loose = write_project(hub_dir / "Loose")
    with pytest.raises(PartOfASongError):
        master.add_local_project(loose, hub_dir, author=BOB)
    assert not (loose / META_DIR).exists()


def test_adding_a_project_already_linked_is_refused_and_its_link_kept(tmp_path, song):
    local = tmp_path / "alice" / "My Song"
    other_hub = tmp_path / "drive" / "Other"
    other_hub.mkdir()
    with pytest.raises(FileExistsError):
        master.add_local_project(local, other_hub, author=ALICE)
    assert (local / META_DIR / "state.db").exists()
    assert not (other_hub / "My Song").exists()


def test_a_failed_add_removes_only_what_it_made(tmp_path, monkeypatch):
    local = write_project(tmp_path / "mine" / "New Song", "New Song")
    hub_dir = tmp_path / "drive" / "Hub"
    hub_dir.mkdir(parents=True)

    def boom(self, **kwargs):
        raise RuntimeError("Drive went away")

    monkeypatch.setattr(Repository, "push", boom)
    with pytest.raises(RuntimeError):
        master.add_local_project(local, hub_dir, author=ALICE)
    assert not (hub_dir / "New Song").exists()
    assert not (local / META_DIR).exists()
    assert (local / "New Song.als").exists()


# --- connecting the song folder instead of the shared folder -----------------


def test_connecting_a_song_folder_lists_just_that_song(song):
    _live_backup_in(song)
    hub = Hub(song)
    names = [s.name for s in hub.songs()]
    assert names == ["My Song"]
    assert hub.songs()[0].kind == "shared"
    assert hub.storage_for("My Song").root == song


def test_connecting_the_data_folder_means_the_song(song):
    hub = Hub(song / DATA_DIR)
    assert hub.path == song
    assert [s.name for s in hub.songs()] == ["My Song"]


def test_a_song_folder_hub_gets_a_working_copy_of_that_song(tmp_path, song):
    hub = Hub(song)
    repo = hub.join_song("My Song", tmp_path / "bob", BOB)
    assert (repo.project_root / "My Song.als").exists()
    assert repo.storage.root == song


def test_a_song_folder_hub_refuses_to_add_other_projects(tmp_path, song):
    other = write_project(tmp_path / "bob" / "Other Song")
    with pytest.raises(PartOfASongError):
        Hub(song).add_song("Other Song", other, BOB)
    assert not (song / "Other Song").exists()


def test_a_normal_hub_never_lists_backup_or_data_folders(hub_dir, song):
    write_project(hub_dir / "Backup", "stray")
    write_project(hub_dir / DATA_DIR, "stray")
    assert [s.name for s in Hub(hub_dir).songs()] == ["My Song"]


# --- a missing history is said plainly -------------------------------------


def test_a_shared_copy_without_its_history_says_so(tmp_path, song):
    import shutil

    shutil.rmtree(song / META_DIR)
    with pytest.raises(SharedHistoryMissingError) as caught:
        Repository.open(tmp_path / "alice" / "My Song", author=ALICE)
    assert "My Song" in str(caught.value)
    assert "untouched" in str(caught.value)


# --- a song Drive has not finished bringing -----------------------------------


@pytest.mark.parametrize(
    "arrange, expect",
    [
        (lambda song: __import__("shutil").rmtree(song / META_DIR), "no .sessionsync folder"),
        (lambda song: (song / META_DIR / "repo.json").unlink(), "not fully arrived"),
        (
            lambda song: ((song / META_DIR / "repo.json").unlink(), (song / META_DIR / "state.db").write_bytes(b"")),
            "leftover",
        ),
    ],
)
def test_getting_a_copy_says_what_is_missing(tmp_path, song, arrange, expect):
    from sessionsync.core.errors import NotAProjectError

    arrange(song)
    with pytest.raises(NotAProjectError) as caught:
        Repository.clone(MasterFolderStorage(song), tmp_path / "sam" / "My Song", author=BOB)
    assert expect in str(caught.value)
    assert "Nothing was changed" in str(caught.value)
