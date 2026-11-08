"""Wrong pushes: a second look before one goes, and undo after."""

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.errors import NothingToPushError
from sessionsync.core.repository import Repository
from sessionsync.storage.master_folder import MasterFolderStorage

from tests.conftest import ALICE, BOB, VOCAL, live_set, record, write_project
from tests.test_master_folder import shared


@pytest.fixture
def master(tmp_path):
    return MasterFolderStorage(tmp_path / "drive" / "My Song")


@pytest.fixture
def alice(tmp_path, master):
    repo = Repository.create(
        write_project(tmp_path / "alice" / "My Song"), master,
        adapter=AbletonAdapter(), author=ALICE,
    )
    repo.push(note="first")
    return repo


@pytest.fixture
def bob(tmp_path, master, alice):
    return Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)


# --- the second look --------------------------------------------------------------


def test_an_ordinary_push_raises_no_warning(alice):
    record(alice.project_root, "Samples/Recorded/Take 2.wav", b"take 2")
    assert alice.push_warnings() == []


def test_removing_the_songs_set_is_questioned(alice):
    (alice.project_root / "Other.als").write_bytes(live_set())
    (alice.project_root / "My Song.als").unlink()
    warnings = alice.push_warnings()
    assert any("removes the song's Live Set" in w for w in warnings)


def test_a_second_set_beside_the_songs_is_questioned_unless_push_merges_it(alice):
    (alice.project_root / "My Song 12.als").write_bytes(live_set("<Copy/>"))
    assert any("another Live Set" in w for w in alice.push_warnings())
    assert alice.push_warnings(merges=[("My Song 12.als", "My Song.als")]) == []


def test_removing_much_of_the_song_is_questioned(alice):
    for n in range(6):
        record(alice.project_root, f"Samples/Recorded/T{n}.wav", b"x" * 100)
    alice.push()
    project = alice.project_root
    (project / "My Song.als").write_bytes(live_set())  # uses only the vocal again
    warnings = alice.push_warnings()
    assert any("removes 6 of the song's" in w for w in warnings)


def test_the_first_push_is_never_questioned(tmp_path, master):
    repo = Repository.create(
        write_project(tmp_path / "x" / "My Song"), master, adapter=AbletonAdapter(), author=ALICE
    )
    assert repo.push_warnings() == []


# --- undo --------------------------------------------------------------------


def test_making_an_old_version_the_latest_again(alice, bob, master):
    vocal = bob.project_root / VOCAL
    record(bob.project_root, VOCAL, b"BROKEN")
    bob.push(note="oops")
    assert shared(master)[VOCAL] == b"BROKEN"

    result = alice.make_latest(1)

    assert result.version == 3
    assert shared(master)[VOCAL] == b"vocal audio"
    entry = {h.version: h for h in alice.history()}[3]
    assert entry.note == "Back to v1"
    assert entry.summary.startswith("Back to v1")
    # the bad push is still there, so the undo can be undone too
    assert 2 in master.list_versions()
    # nobody's copy changes until they pull
    assert vocal.read_bytes() == b"BROKEN"
    bob.pull()
    assert vocal.read_bytes() == b"vocal audio"
    assert not bob.status().diff.has_changes


def test_making_the_latest_the_latest_does_nothing(alice):
    with pytest.raises(NothingToPushError):
        alice.make_latest(1)


def test_an_unknown_version_cannot_be_made_the_latest(alice):
    with pytest.raises(ValueError):
        alice.make_latest(7)


# --- the windows --------------------------------------------------------------


@pytest.fixture
def qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_history_window_lists_versions_and_offers_undo_for_older_ones(qt_app, alice, bob):
    from sessionsync.ui.history import HistoryDialog

    record(bob.project_root, "Samples/Recorded/Sax.wav", b"sax")
    bob.push(note="sax idea")
    dialog = HistoryDialog(None, alice.history(), "My Song")

    assert dialog.list.count() == 2
    assert "v2 · Bob" in dialog.list.item(0).text() and "(latest)" in dialog.list.item(0).text()
    assert "sax idea" in dialog.list.item(0).text()
    assert "Sax.wav" in dialog.details.toPlainText()
    assert not dialog.latest_button.isEnabled()  # already the latest
    dialog.list.setCurrentRow(1)
    assert dialog.latest_button.isEnabled()
    assert dialog.restore_button.isEnabled()


def test_push_dialog_asks_once_more_for_a_push_that_looks_wrong(qt_app, alice, monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton

    from sessionsync.ui import dialogs
    from sessionsync.ui.engine import Engine

    (alice.project_root / "Other.als").write_bytes(live_set())
    (alice.project_root / "My Song.als").unlink()
    seen = []
    engine = Engine(alice.project_root)
    engine.snapshot_ready.connect(seen.append)
    engine._repo = alice
    engine._emit_snapshot()
    snapshot = seen[-1]
    assert snapshot.push_warnings

    def look(dialog):
        boxes = {b.text().split(" (")[0]: b for b in dialog.findChildren(QCheckBox)}
        push = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Push")
        boxes["I saved the set in Live"].setChecked(True)
        assert not push.isEnabled()
        boxes["Yes, push it like this"].setChecked(True)
        assert push.isEnabled()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", look)
    assert dialogs.ask_push(None, snapshot) is None


# --- the shared history lost ------------------------------------------------------


def lose_history(master):
    import shutil

    shutil.rmtree(master.meta)


def test_sharing_again_puts_back_the_history_without_uploading_the_song(alice, bob, master):
    from sessionsync.core.errors import SharedHistoryMissingError

    record(bob.project_root, "Samples/Recorded/Sax.wav", b"sax")
    bob.push(note="sax")
    alice.pull()
    lose_history(master)
    with pytest.raises(SharedHistoryMissingError):
        Repository.open(alice.project_root, author=ALICE)
    uploads = []
    original = MasterFolderStorage.upload_object
    MasterFolderStorage.upload_object = lambda self, sha, src: (uploads.append(src), original(self, sha, src))
    try:
        repo = Repository.reshare(alice.project_root, author=ALICE)
    finally:
        MasterFolderStorage.upload_object = original

    assert uploads == []  # every file was still in the song folder
    assert master.list_versions() == [2]
    assert not repo.status().diff.has_changes
    # Bob carries on as if nothing happened
    bob = Repository.open(bob.project_root, author=BOB)
    assert bob.status().state == "up_to_date"
    record(bob.project_root, "Samples/Recorded/Sax 2.wav", b"sax 2")
    assert bob.push().version == 3


def test_without_a_kept_manifest_this_copy_is_shared_as_the_next_version(alice, bob, master):
    record(alice.project_root, "Samples/Recorded/Mine.wav", b"mine")
    lose_history(master)
    alice.state._set("base_manifest", None)

    repo = Repository.reshare(alice.project_root, author=ALICE)

    assert master.list_versions() == [2]
    assert repo.base_version == 2
    assert shared(master)["Samples/Recorded/Mine.wav"] == b"mine"
    bob = Repository.open(bob.project_root, author=BOB)
    bob.pull()
    assert (bob.project_root / "Samples/Recorded/Mine.wav").read_bytes() == b"mine"


def test_sharing_again_refuses_a_song_whose_history_is_there(alice):
    with pytest.raises(ValueError):
        Repository.reshare(alice.project_root, author=ALICE)
