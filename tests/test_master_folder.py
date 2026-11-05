"""The shared copy: a normal project folder that only changes when a put-back succeeds."""

import shutil

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.errors import StalePushError
from sessionsync.core.hashing import hash_file
from sessionsync.core.repository import Repository
from sessionsync.storage.base import NotReadyError
from sessionsync.storage.master_folder import MasterFolderStorage

from tests.conftest import ALICE, BOB, write_project


def snapshot(folder):
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and ".sessionsync" not in p.parts
    }


@pytest.fixture
def master(tmp_path):
    return MasterFolderStorage(tmp_path / "drive" / "My Song")


@pytest.fixture
def alice_wb(tmp_path, master):
    repo = Repository.create(
        write_project(tmp_path / "alice" / "My Song"), master,
        adapter=AbletonAdapter(), author=ALICE,
    )
    repo.push(note="first")
    return repo


@pytest.fixture
def bob_wb(tmp_path, master, alice_wb):
    return Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)


def edit(repo, name="Samples/Recorded/Vocal.wav", data=b"new take"):
    (repo.project_root / name).write_bytes(data)


def test_master_is_a_normal_project_after_first_put_back(master, alice_wb):
    assert snapshot(master.root) == snapshot(alice_wb.project_root)
    assert master.current_version() == 1


def test_creating_storage_touches_nothing(tmp_path):
    MasterFolderStorage(tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()


def test_master_is_untouched_until_put_back(master, alice_wb):
    before = snapshot(master.root)
    edit(alice_wb)
    alice_wb.status()
    assert snapshot(master.root) == before


def test_put_back_replaces_master_and_keeps_old_in_history(master, alice_wb):
    old = (master.root / "Samples/Recorded/Vocal.wav").read_bytes()
    edit(alice_wb)
    alice_wb.push(note="new vocal")
    assert (master.root / "Samples/Recorded/Vocal.wav").read_bytes() == b"new take"
    assert master.current_version() == 2
    copy = alice_wb.restore_copy(1, alice_wb.project_root.parent / "v1")
    assert (copy / "Samples/Recorded/Vocal.wav").read_bytes() == old


def test_stale_put_back_is_refused_and_master_unchanged(master, alice_wb, bob_wb):
    edit(alice_wb)
    alice_wb.push()
    before = snapshot(master.root)
    edit(bob_wb, data=b"bob take")
    with pytest.raises(StalePushError):
        bob_wb.push()
    assert snapshot(master.root) == before


def test_get_latest_brings_other_persons_version(master, alice_wb, bob_wb):
    edit(alice_wb)
    alice_wb.push()
    bob_wb.pull()
    assert (bob_wb.project_root / "Samples/Recorded/Vocal.wav").read_bytes() == b"new take"


def test_deleted_file_goes_to_history(master, alice_wb):
    (alice_wb.project_root / "Samples/Recorded/Vocal.wav").unlink()
    alice_wb.push()
    assert not (master.root / "Samples/Recorded/Vocal.wav").exists()
    copy = alice_wb.restore_copy(1, alice_wb.project_root.parent / "back")
    assert (copy / "Samples/Recorded/Vocal.wav").read_bytes() == b"vocal audio"


def test_only_last_five_versions_are_kept(master, alice_wb):
    for i in range(7):
        edit(alice_wb, data=f"take {i}".encode())
        alice_wb.push()
    assert master.current_version() == 8
    assert master.pruned_versions() == {1, 2, 3}
    alice_wb.restore_copy(4, alice_wb.project_root.parent / "v4")
    with pytest.raises(Exception):
        alice_wb.restore_copy(2, alice_wb.project_root.parent / "v2")
    # history holds only what the kept versions need
    objects = list((master.meta / "history" / "objects").glob("*/*"))
    assert len(objects) == 4  # takes 0..3 replaced; take 6 is the live file, 4 and 5 replaced too


def test_pinned_version_survives_pruning(master, alice_wb):
    master.pin_version(1)
    for i in range(7):
        edit(alice_wb, data=f"take {i}".encode())
        alice_wb.push()
    assert 1 not in master.pruned_versions()
    copy = alice_wb.restore_copy(1, alice_wb.project_root.parent / "v1")
    assert (copy / "Samples/Recorded/Vocal.wav").read_bytes() == b"vocal audio"


def test_drive_still_syncing_blocks_pull_cleanly(master, alice_wb, bob_wb):
    edit(alice_wb)
    alice_wb.push()
    victim = master.root / "Samples/Recorded/Vocal.wav"
    victim.write_bytes(b"new")  # right name, wrong size: bytes have not all arrived
    before = snapshot(bob_wb.project_root)
    with pytest.raises(NotReadyError):
        bob_wb.pull()
    assert snapshot(bob_wb.project_root) == before


def test_missing_file_blocks_pull_cleanly(master, alice_wb, bob_wb):
    edit(alice_wb)
    alice_wb.push()
    (master.root / "Samples/Recorded/Vocal.wav").unlink()
    with pytest.raises(NotReadyError):
        bob_wb.pull()


class Boom(Exception):
    pass


@pytest.mark.parametrize("step", ["to_history", "place", "marker"])
def test_crash_during_commit_is_finished_by_next_client(monkeypatch, tmp_path, master, alice_wb, bob_wb, step):
    edit(alice_wb, data=b"take A")
    target = {
        "to_history": MasterFolderStorage._to_history,
        "place": MasterFolderStorage._place,
        "marker": MasterFolderStorage._prune,
    }[step]
    name = {"to_history": "_to_history", "place": "_place", "marker": "_prune"}[step]

    def explode(self, *a, **k):
        raise Boom()

    monkeypatch.setattr(MasterFolderStorage, name, explode)
    monkeypatch.setattr(MasterFolderStorage, "_release_lock", lambda self: None)  # a dead process
    with pytest.raises(Boom):
        alice_wb.push()
    monkeypatch.setattr(MasterFolderStorage, name, target)
    monkeypatch.undo()

    # the dead client's lock is stale
    lock = master.meta / "staging" / "commit.lock"
    import os, time
    old = time.time() - 3600
    if lock.exists():
        os.utime(lock, (old, old))

    fresh = MasterFolderStorage(master.root)
    assert fresh.current_version() == 2
    assert (master.root / "Samples/Recorded/Vocal.wav").read_bytes() == b"take A"
    bob_wb.pull()
    assert (bob_wb.project_root / "Samples/Recorded/Vocal.wav").read_bytes() == b"take A"
    copy = bob_wb.restore_copy(1, tmp_path / "old")
    assert (copy / "Samples/Recorded/Vocal.wav").read_bytes() == b"vocal audio"


def test_live_put_back_is_not_disturbed(master, alice_wb, bob_wb):
    (master.meta / "staging").mkdir(exist_ok=True)
    (master.meta / "staging" / "commit.lock").write_text("{}")
    (master.meta / "manifests" / "v000002.json").write_bytes(b"{}")
    assert master.current_version() == 1  # fresh lock: leave the other client alone
    assert (master.meta / "manifests" / "v000002.json").exists()


def test_adoption_leaves_visible_files_untouched(tmp_path):
    from sessionsync.core.master import adopt

    folder = write_project(tmp_path / "drive" / "Their Song")
    before = snapshot(folder)
    mtimes = {p: p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file()}
    adopt(folder, author=ALICE)
    assert snapshot(folder) == before
    assert {p: p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file() and ".sessionsync" not in p.parts} == mtimes
    master = MasterFolderStorage(folder)
    assert master.current_version() == 1
    assert not (folder / ".sessionsync" / "state.db").exists()


def test_get_it_from_an_adopted_master_and_put_back(tmp_path):
    from sessionsync.core.master import adopt, get_workbench

    folder = write_project(tmp_path / "drive" / "Their Song")
    adopt(folder, author=ALICE)
    wb = get_workbench(folder, tmp_path / "local" / "Their Song", author=BOB)
    assert snapshot(wb.project_root) == snapshot(folder)
    edit(wb, data=b"bob")
    wb.push()
    assert (folder / "Samples/Recorded/Vocal.wav").read_bytes() == b"bob"
    assert MasterFolderStorage(folder).current_version() == 2


def test_add_local_project_to_hub(tmp_path):
    from sessionsync.core.master import add_local_project

    local = write_project(tmp_path / "anywhere" / "Mine")
    hub = tmp_path / "drive" / "Sam Stefan Sketches"
    hub.mkdir(parents=True)
    repo = add_local_project(local, hub, author=ALICE)
    shared = hub / "Mine"
    assert snapshot(shared) == snapshot(local)
    assert repo.base_version == 1
    assert MasterFolderStorage(shared).current_version() == 1


def test_add_refuses_existing_name_and_leaves_no_skeleton(tmp_path):
    from sessionsync.core.master import add_local_project

    local = write_project(tmp_path / "anywhere" / "Mine")
    hub = tmp_path / "drive"
    (hub / "Mine").mkdir(parents=True)
    (hub / "Mine" / "x.txt").write_text("x")
    with pytest.raises(FileExistsError):
        add_local_project(local, hub, author=ALICE)
    assert sorted(p.name for p in (hub / "Mine").iterdir()) == ["x.txt"]


def test_kind_detection(tmp_path):
    from sessionsync.core.master import adopt, folder_kind

    folder = write_project(tmp_path / "drive" / "Song")
    assert folder_kind(folder) == "unmanaged"
    adopt(folder, author=ALICE)
    assert folder_kind(folder) == "master"
    wb = Repository.open  # noqa
    from sessionsync.core.master import get_workbench

    w = get_workbench(folder, tmp_path / "loc" / "Song", author=BOB)
    assert folder_kind(w.project_root) == "workbench"
