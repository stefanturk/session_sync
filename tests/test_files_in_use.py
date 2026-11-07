"""Windows: Live keeps a loaded song's audio files open and nobody else may read them.

Sam's first look at a freshly downloaded song happened with Live already open,
so every check failed with "[Errno 13] Permission denied" — again and again.
"""

import pytest

from sessionsync.core import hashing, master
from sessionsync.core.errors import FileInUseError
from sessionsync.core.repository import Repository
from sessionsync.storage.master_folder import MasterFolderStorage

from tests.conftest import ALICE, BOB, VOCAL, record, write_project


@pytest.fixture
def song(tmp_path):
    hub = tmp_path / "drive" / "Hub"
    hub.mkdir(parents=True)
    master.add_local_project(write_project(tmp_path / "alice" / "My Song"), hub, author=ALICE)
    return hub / "My Song"


@pytest.fixture
def live_holds(monkeypatch):
    """Make reading any file whose name is in the set fail as Windows does."""
    held: set[str] = set()
    real = hashing.hash_file

    def hash_file(path):
        if path.name in held:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(hashing, "hash_file", hash_file)
    return held


def test_a_fresh_copy_is_never_read_again(tmp_path, song, monkeypatch):
    sam = Repository.clone(MasterFolderStorage(song), tmp_path / "sam" / "My Song", author=BOB)

    def no_reading(path):
        raise AssertionError(f"read {path} again")

    monkeypatch.setattr(hashing, "hash_file", no_reading)
    assert sam.status().state == "up_to_date"


def test_a_check_while_live_holds_the_audio_does_not_fail(tmp_path, song, live_holds):
    sam = Repository.clone(MasterFolderStorage(song), tmp_path / "sam" / "My Song", author=BOB)
    sam.hash_cache = hashing.HashCache(tmp_path / "empty-cache.db")  # as if never checked
    live_holds.add("Vocal.wav")
    assert sam.status().state == "up_to_date"
    assert VOCAL in sam.in_use


def test_a_new_recording_live_is_still_writing_shows_as_a_change(tmp_path, song, live_holds):
    sam = Repository.clone(MasterFolderStorage(song), tmp_path / "sam" / "My Song", author=BOB)
    record(sam.project_root, "Samples/Recorded/Take 1.wav", b"take one")
    live_holds.add("Take 1.wav")
    status = sam.status()
    assert status.state == "local_changes"
    assert "Samples/Recorded/Take 1.wav" in status.diff.added


def test_push_says_to_close_the_song_in_live(tmp_path, song, live_holds):
    sam = Repository.clone(MasterFolderStorage(song), tmp_path / "sam" / "My Song", author=BOB)
    record(sam.project_root, "Samples/Recorded/Take 1.wav", b"take one")
    live_holds.add("Take 1.wav")
    with pytest.raises(FileInUseError) as caught:
        sam.push(note="take")
    assert "Close the song in Live" in str(caught.value)
    assert MasterFolderStorage(song).current_version() == 1
