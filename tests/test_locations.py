"""Your copy of a song lives on this computer only, somewhere obvious."""

from sessionsync.core import locations


def test_new_songs_go_on_the_desktop(no_real_desktop):
    assert locations.default_root() == no_real_desktop / "Desktop"


def test_a_desktop_that_icloud_syncs_is_avoided(no_real_desktop):
    (no_real_desktop / "Library/Mobile Documents/com~apple~CloudDocs/Desktop").mkdir(parents=True)
    assert locations.default_root() == no_real_desktop / "Music" / "Session Sync"
    verdict = locations.check_location(no_real_desktop / "Desktop")
    assert verdict.ok and "iCloud" in verdict.warning


def test_synced_folders_are_refused(tmp_path):
    for synced in (
        tmp_path / "My Drive (me@example.com)" / "Songs",
        tmp_path / "Library" / "CloudStorage" / "GoogleDrive-me" / "x",
        tmp_path / "Dropbox" / "Music",
        tmp_path / "OneDrive - Work",
    ):
        verdict = locations.check_location(synced)
        assert not verdict.ok and "synced" in verdict.reason


def test_inside_a_shared_folder_or_another_song_is_refused(tmp_path):
    hub, song = tmp_path / "hub", tmp_path / "songs" / "A"
    hub.mkdir(); song.mkdir(parents=True)
    assert not locations.check_location(hub / "x", hubs=[hub]).ok
    assert not locations.check_location(song / "inner", songs=[song]).ok
    assert locations.check_location(tmp_path / "songs", songs=[song]).ok


def test_an_existing_folder_is_never_reused(tmp_path):
    (tmp_path / "Song").mkdir()
    assert locations.free_folder(tmp_path, "Song") == tmp_path / "Song (Session Sync)"
    (tmp_path / "Song (Session Sync)").mkdir()
    assert locations.free_folder(tmp_path, "Song") == tmp_path / "Song (Session Sync) 2"
    assert locations.free_folder(tmp_path, "Other") == tmp_path / "Other"


def test_room_is_checked_with_space_to_record(tmp_path):
    enough, free = locations.room_for(tmp_path / "not yet", 1)
    assert free > 0
    assert locations.room_for(tmp_path, free)[0] is False  # needs the spare too


def test_the_top_of_a_cloud_drive_is_recognised(tmp_path):
    for top in (
        tmp_path / "My Drive (me@example.com)",
        tmp_path / "Library" / "CloudStorage" / "GoogleDrive-me",
        tmp_path / "Library" / "CloudStorage" / "GoogleDrive-me" / "My Drive",
        tmp_path / "Dropbox",
        tmp_path / "OneDrive - Work",
        tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
    ):
        assert locations.is_cloud_root(top), top
    for inside in (
        tmp_path / "My Drive (me@example.com)" / "Song Collaboration",
        tmp_path / "Library" / "CloudStorage" / "GoogleDrive-me" / "My Drive" / "Songs",
        tmp_path / "Dropbox" / "Music",
        tmp_path / "Desktop",
    ):
        assert not locations.is_cloud_root(inside), inside


def test_nothing_is_ever_written_to_the_top_of_a_drive(tmp_path):
    import pytest
    from sessionsync.core.errors import CloudRootError
    from sessionsync.storage.master_folder import MasterFolderStorage
    from sessionsync.ui.settings import Settings

    top = tmp_path / "My Drive (me@example.com)"
    top.mkdir()
    with pytest.raises(CloudRootError):
        MasterFolderStorage(top)._ensure_layout()
    assert list(top.iterdir()) == []
    with pytest.raises(CloudRootError):
        Settings(tmp_path / "ui.json").add_hub(top)
    (top / "Song Collaboration").mkdir()
    MasterFolderStorage(top / "Song Collaboration" / "Song")._ensure_layout()  # fine
