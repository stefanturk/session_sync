"""Joining a collaborator: the invite message, finding the shared folder in Drive."""

import sqlite3

from sessionsync.core import drivelink

from tests.conftest import ALICE, write_project


def test_shared_folders_are_found_in_drive(tmp_path):
    from sessionsync.core.hub import Hub

    drive = tmp_path / "My Drive"
    hub_dir = drive / "Song Collaboration" / "Sam Stefan Sketches"
    hub_dir.mkdir(parents=True)
    Hub(hub_dir).add_song("Song A", write_project(tmp_path / "me" / "Song A", "Song A"), ALICE)
    (drive / "Photos" / "2019").mkdir(parents=True)

    assert drivelink.find_shared_folders([drive]) == [hub_dir]
    assert drivelink.find_shared_folders([tmp_path / "elsewhere"]) == []


def fake_drivefs(root, rows, parents):
    db = root / "123" / "metadata_sqlite_db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE items (stable_id INTEGER, id TEXT, local_title TEXT, is_folder BOOLEAN, trashed BOOLEAN)")
        conn.execute("CREATE TABLE stable_parents (item_stable_id INTEGER, parent_stable_id INTEGER)")
        conn.executemany("INSERT INTO items VALUES (?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO stable_parents VALUES (?,?)", parents)


def test_a_folders_web_link_comes_from_drives_own_records(tmp_path, monkeypatch):
    fake_drivefs(
        tmp_path / "fs",
        [
            (1, "0AROOT", "My Drive", 1, 0),
            (2, "idCollab", "Song Collaboration", 1, 0),
            (3, "idTrashed", "Sam Stefan Sketches", 1, 1),
            (4, "idHub", "Sam Stefan Sketches", 1, 0),
            (5, "idOther", "Sam Stefan Sketches", 1, 0),  # same name somewhere else
            (6, "idElse", "Elsewhere", 1, 0),
        ],
        [(2, 1), (3, 2), (4, 2), (5, 6), (6, 1)],
    )
    monkeypatch.setattr(drivelink, "drivefs_dir", lambda: tmp_path / "fs")
    path = tmp_path / "My Drive (me@gmail.com)" / "Song Collaboration" / "Sam Stefan Sketches"
    assert drivelink.folder_link(path) == "https://drive.google.com/drive/folders/idHub"
    assert drivelink.folder_link(tmp_path / "My Drive" / "Nope") is None


def test_a_shortcut_target_path_carries_its_id():
    path = drivelink.Path("G:/.shortcut-targets-by-id/1AbC/Sam Stefan Sketches")
    assert drivelink.folder_id(path) == "1AbC"


def test_the_invite_says_everything_needed():
    text = drivelink.invite_text("Sam Stefan Sketches", "https://drive.google.com/drive/folders/x", "Stefan")
    assert "Google Drive for desktop" in text and drivelink.DOWNLOAD_URL in text
    assert "https://drive.google.com/drive/folders/x" in text and "Add shortcut" in text
    assert "install.ps1" in text and "install.sh" in text and "session_sync" in text
    assert "Connect a shared folder" in text


def test_drives_upload_queue_is_read(tmp_path, monkeypatch):
    account = tmp_path / "fs" / "123"
    account.mkdir(parents=True)
    with sqlite3.connect(account / "mirror_sqlite.db") as conn:
        conn.execute("CREATE TABLE queued_uploads (stable_id INTEGER, local_stable_id INTEGER, size INTEGER, md5_checksum TEXT, mtime_ms INTEGER)")
        conn.execute("CREATE TABLE pending_uploads (local_stable_id INTEGER, stable_id INTEGER)")
        conn.executemany("INSERT INTO queued_uploads VALUES (?,?,?,?,?)", [(1, 1, 1000, "", 0), (2, 2, 500, "", 0)])
    monkeypatch.setattr(drivelink, "drivefs_dir", lambda: tmp_path / "fs")
    assert drivelink.uploads_pending() == (2, 1500)
    monkeypatch.setattr(drivelink, "drivefs_dir", lambda: tmp_path / "none")
    assert drivelink.uploads_pending() is None
