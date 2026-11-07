import pytest

from tests.conftest import als_with_tracks, record


def test_restore_copy_rebuilds_an_old_version_without_touching_the_project(alice, tmp_path):
    root = alice.project_root
    record(root, "Samples/Recorded/Vocal.wav", b"BROKEN")
    alice.push(note="oops")
    assert alice.base_version == 2

    copy = alice.restore_copy(1, tmp_path / "restored v1")

    assert (copy / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"vocal audio"
    assert (root / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"BROKEN"
    assert alice.base_version == 2  # nothing about the project moved


def test_restore_copy_refuses_an_existing_folder(alice, tmp_path):
    (tmp_path / "taken").mkdir()
    with pytest.raises(FileExistsError):
        alice.restore_copy(1, tmp_path / "taken")


def test_restore_copy_unknown_version(alice, tmp_path):
    with pytest.raises(ValueError):
        alice.restore_copy(9, tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_history_carries_summaries(alice):
    (alice.project_root / "My Song.als").write_bytes(als_with_tracks({"Drums": "1"}))
    alice.push()
    (alice.project_root / "My Song.als").write_bytes(als_with_tracks({"Drums": "2", "Sax": "1"}))
    alice.push()
    summaries = {h.version: h.summary for h in alice.history()}
    assert summaries[1].startswith("First version")
    assert summaries[2] == "1 file changed"  # v1 had no track data to compare with
    assert summaries[3] == "Added Sax · Changed Drums"


def test_versions_after(alice):
    record(alice.project_root, "Samples/Recorded/More.wav", b"m")
    alice.push()
    assert [h.version for h in alice.versions_after(1)] == [2]
    assert alice.versions_after(2) == []
