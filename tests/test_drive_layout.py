"""The shared copy in Drive: nothing in it that Live would open, and a rescue for when someone did."""

import json
import os
import plistlib
import time

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core import launcher
from sessionsync.core.errors import DriveEditsError, NewerFormatError
from sessionsync.core.hashing import hash_file
from sessionsync.core.repository import Repository
from sessionsync.storage.master_folder import DATA_DIR, MasterFolderStorage

from tests.conftest import ALICE, BOB, als_marked, write_project

SET = "My Song.als"


@pytest.fixture
def master(tmp_path):
    return MasterFolderStorage(tmp_path / "drive" / "My Song")


@pytest.fixture
def alice(tmp_path, master):
    repo = Repository.create(
        write_project(tmp_path / "alice" / "My Song"), master, adapter=AbletonAdapter(), author=ALICE
    )
    repo.push(note="first")
    return repo


@pytest.fixture
def bob(tmp_path, master, alice):
    return Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)


def drive_set(master):
    return master.root / DATA_DIR / "My Song.als-shared"


def save_in_drive(master, data=b"", *, name=None):
    """What Live does when someone opens the shared copy and presses Save."""
    target = master.root / DATA_DIR / name if name else drive_set(master)
    target.write_bytes(data or als_marked("tempo 128"))
    backup = master.root / DATA_DIR / "Backup"
    backup.mkdir(exist_ok=True)
    (backup / "My Song [2026-11-06 174800].als").write_bytes(b"live's own backup")


# --- the layout --------------------------------------------------------------


def test_drive_holds_no_live_set_and_has_a_front_door(master, alice):
    assert drive_set(master).exists()
    assert not list(master.root.rglob("*.als"))
    assert (master.root / DATA_DIR / "Samples/Recorded/Vocal.wav").exists()
    link = plistlib.loads((master.root / launcher.MAC_LAUNCHER).read_bytes())["URL"]
    assert launcher.parse_link(link) == (alice.state.repository_id, "My Song")
    assert "double-click" in (master.root / launcher.READ_ME).read_text()


def test_the_front_door_is_not_rewritten_when_nothing_changed(master, alice):
    door = master.root / launcher.MAC_LAUNCHER
    old = time.time() - 3600
    os.utime(door, (old, old))
    (alice.project_root / SET).write_bytes(als_marked("v2"))
    alice.push()
    assert door.stat().st_mtime == pytest.approx(old, abs=1)  # Drive is not told to upload it again


def test_links_that_are_not_ours_are_ignored():
    assert launcher.parse_link("https://example.com") is None
    assert launcher.parse_link("sessionsync://open?song=x") is None
    assert launcher.parse_link(launcher.link_for("id 1", "Söng & Co")) == ("id 1", "Söng & Co")


def test_a_shared_copy_from_a_newer_app_is_left_alone(master, alice, bob):
    meta = master.meta / "repo.json"
    data = json.loads(meta.read_text())
    data["repository_format_version"] = 99
    meta.write_text(json.dumps(data))
    (bob.project_root / SET).write_bytes(als_marked("bob"))
    with pytest.raises(NewerFormatError):
        bob.push()
    with pytest.raises(NewerFormatError):
        bob.pull()


# --- moving an older shared copy -------------------------------------------------


def make_format_1(master):
    """Lay the shared copy out the way the first Session Sync did."""
    data = master.root / DATA_DIR
    for path in sorted(data.rglob("*"), reverse=True):
        if path.is_file():
            rel = path.relative_to(data).as_posix()
            dest = master.root / (rel[: -len("-shared")] if rel.endswith(".als-shared") else rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, dest)
    import shutil

    shutil.rmtree(data)
    for name in launcher.FRONT_DOOR:
        (master.root / name).unlink()
    meta = master.meta / "repo.json"
    info = json.loads(meta.read_text())
    info["repository_format_version"] = 1
    meta.write_text(json.dumps(info))
    assert master.layout() == 1


def test_an_older_shared_copy_is_still_read_and_moved_on_the_next_push(master, alice, bob):
    make_format_1(master)
    (master.root / "Backup").mkdir()
    (master.root / "Backup" / "old.als").write_bytes(b"live backup")

    (alice.project_root / SET).write_bytes(als_marked("v2"))
    alice.push(note="v2")

    assert master.layout() == 2
    assert not [p for p in master.root.rglob("*.als") if ".sessionsync" not in p.parts]
    assert (master.root / launcher.MAC_LAUNCHER).exists()
    assert not (master.root / "Backup").exists()  # tidied into the shared trash, not deleted
    assert list((master.meta / "trash").rglob("old.als"))
    assert not (master.root / "Samples").exists()
    bob.pull()
    assert (bob.project_root / SET).read_bytes() == als_marked("v2")


def test_a_move_cut_short_is_finished_by_the_next_computer(monkeypatch, master, alice):
    make_format_1(master)

    def boom(self):
        raise RuntimeError("power cut")

    monkeypatch.setattr(MasterFolderStorage, "ensure_front_door", boom)
    monkeypatch.setattr(MasterFolderStorage, "_release_lock", lambda self: None)
    with pytest.raises(RuntimeError):
        MasterFolderStorage(master.root).migrate()
    monkeypatch.undo()
    lock = master.meta / "staging" / "commit.lock"
    old = time.time() - 3600
    os.utime(lock, (old, old))

    fresh = MasterFolderStorage(master.root)
    assert fresh.current_version() == 1
    assert fresh.layout() == 2
    assert (master.root / launcher.MAC_LAUNCHER).exists()
    assert drive_set(master).exists()


def test_a_set_edited_in_an_older_shared_copy_is_caught_before_the_move(master, alice):
    make_format_1(master)
    (master.root / SET).write_bytes(als_marked("saved in drive"))
    assert alice.drive_edits() == [SET]
    with pytest.raises(DriveEditsError):
        alice.push()

    rescued = alice.rescue_drive_edits()
    assert rescued[0].replaced
    assert (alice.project_root / SET).read_bytes() == als_marked("saved in drive")
    alice.push(note="rescued")
    assert master.layout() == 2
    assert hash_file(drive_set(master)) == alice.read_manifest(2).files[SET].sha256


# --- edits saved straight into Drive ---------------------------------------------


def test_a_set_saved_in_drive_is_noticed(master, alice, bob):
    save_in_drive(master)
    assert alice.status().drive_edits == [f"{DATA_DIR}/My Song.als-shared"]
    with pytest.raises(DriveEditsError):
        alice.push()


def test_a_pull_of_a_set_saved_in_drive_explains_itself(master, alice, bob):
    (alice.project_root / "Samples/Recorded/Vocal.wav").write_bytes(b"v2")
    (alice.project_root / SET).write_bytes(als_marked("v2"))
    alice.push()
    save_in_drive(master)
    with pytest.raises(DriveEditsError):
        bob.pull()


def test_rescue_brings_the_edit_home_and_puts_drive_right(master, alice, bob):
    original = drive_set(master).read_bytes()
    save_in_drive(master)

    rescued = alice.rescue_drive_edits()

    assert [r.replaced for r in rescued] == [True]
    assert (alice.project_root / SET).read_bytes() == als_marked("tempo 128")
    assert drive_set(master).read_bytes() == original           # Drive is the pushed version again
    assert not (master.root / DATA_DIR / "Backup").exists()     # Live's backups tidied away
    assert list((master.meta / "rescued").rglob("My Song.als"))  # and the edit kept in Drive too
    assert list(alice.state.trash_dir.rglob(SET))               # my old set, recoverable
    assert alice.drive_edits() == []
    assert bob.rescue_drive_edits() == []                        # a second computer finds nothing to do

    alice.push(note="tempo")
    bob.pull()
    assert (bob.project_root / SET).read_bytes() == als_marked("tempo 128")


def test_rescue_never_overwrites_work_of_mine(master, alice):
    (alice.project_root / SET).write_bytes(als_marked("my unpushed work"))
    save_in_drive(master)

    rescued = alice.rescue_drive_edits()

    assert not rescued[0].replaced
    assert rescued[0].path.name.startswith("My Song (edited in Drive, ")
    assert rescued[0].path.read_bytes() == als_marked("tempo 128")
    assert (alice.project_root / SET).read_bytes() == als_marked("my unpushed work")


def test_a_set_saved_under_a_new_name_in_drive_is_rescued_beside_mine(master, alice):
    (master.root / DATA_DIR / "My Song v2.als").write_bytes(als_marked("save as"))
    assert alice.drive_edits() == [f"{DATA_DIR}/My Song v2.als"]

    rescued = alice.rescue_drive_edits()

    assert not rescued[0].replaced
    assert rescued[0].path.read_bytes() == als_marked("save as")
    assert not (master.root / DATA_DIR / "My Song v2.als").exists()


def test_rescue_restores_drive_from_my_copy_when_drive_had_the_only_one(master, alice):
    """Live saved over the only shared copy of the set; my own copy has the real bytes."""
    save_in_drive(master)
    pushed = alice.read_manifest(1).files[SET].sha256
    alice.rescue_drive_edits()
    assert hash_file(drive_set(master)) == pushed


# --- the command line ---------------------------------------------------------


def test_cli_rescue_and_open(master, alice):
    from typer.testing import CliRunner

    from sessionsync.cli import app

    runner = CliRunner()
    save_in_drive(master)
    status = runner.invoke(app, ["status", "--project", str(alice.project_root)])
    assert "sessionsync rescue" in status.output
    result = runner.invoke(app, ["rescue", "--project", str(alice.project_root)])
    assert result.exit_code == 0 and "now has the edit" in result.output
    again = runner.invoke(app, ["rescue", "--project", str(alice.project_root)])
    assert "Nothing to rescue" in again.output
    opened = runner.invoke(app, ["open", "--no-open", "--project", str(alice.project_root)])
    assert opened.exit_code == 0 and "My Song.als" in opened.output
