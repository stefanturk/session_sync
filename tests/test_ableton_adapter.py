import gzip

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter


@pytest.fixture
def adapter():
    return AbletonAdapter()


def make_project(root, name="My Song", *, extra=()):
    """A minimal but realistic Ableton project folder."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.als").write_bytes(gzip.compress(b"<Ableton MinorVersion='11.0_11300'/>"))
    (root / "Ableton Project Info").mkdir(exist_ok=True)
    (root / "Ableton Project Info" / "Project11.cfg").write_bytes(b"cfg")
    (root / "Samples" / "Recorded").mkdir(parents=True, exist_ok=True)
    (root / "Samples" / "Recorded" / "Vocal.wav").write_bytes(b"wav data")
    for rel in extra:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return root


class TestDetect:
    def test_detects_a_folder_containing_a_live_set(self, adapter, tmp_path):
        assert adapter.detect_project(make_project(tmp_path / "My Song"))

    def test_detects_a_project_folder_whose_set_has_been_removed(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")
        (root / "My Song.als").unlink()

        assert adapter.detect_project(root)

    def test_rejects_an_unrelated_folder(self, adapter, tmp_path):
        plain = tmp_path / "Documents"
        plain.mkdir()
        (plain / "notes.txt").write_text("nothing musical here")

        assert not adapter.detect_project(plain)


class TestEnumerateTrackedFiles:
    def test_tracks_the_set_project_info_and_samples(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")

        tracked = adapter.enumerate_tracked_files(root)

        assert set(tracked) == {
            root / "My Song.als",
            root / "Ableton Project Info" / "Project11.cfg",
            root / "Samples" / "Recorded" / "Vocal.wav",
        }

    @pytest.mark.parametrize(
        "junk",
        [
            "Backup/My Song [2026-08-16 210000].als",
            ".DS_Store",
            "Samples/._Vocal.wav",
            "Samples/Recorded/Vocal.wav.asd",
            ".sessionsync/state.db",
            "My Song.als.tmp",
        ],
    )
    def test_excludes_files_ableton_or_macos_regenerates(self, adapter, tmp_path, junk):
        root = make_project(tmp_path / "My Song", extra=[junk])

        tracked = adapter.enumerate_tracked_files(root)

        assert root / junk not in tracked

    def test_tracks_media_the_user_dropped_anywhere_in_the_project(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song", extra=["Extra Stems/Guitar.aif"])

        assert root / "Extra Stems" / "Guitar.aif" in adapter.enumerate_tracked_files(root)

    def test_returns_files_in_a_stable_order(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")

        assert adapter.enumerate_tracked_files(root) == sorted(adapter.enumerate_tracked_files(root))


class TestValidate:
    def test_a_normal_project_is_valid_without_complaints(self, adapter, tmp_path):
        result = adapter.validate_project(make_project(tmp_path / "My Song"))

        assert result.is_valid
        assert result.errors == []

    def test_a_folder_with_no_live_set_is_invalid(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")
        (root / "My Song.als").unlink()

        result = adapter.validate_project(root)

        assert not result.is_valid
        assert any("Live Set" in e for e in result.errors)

    def test_several_live_sets_is_valid_but_warns_which_one_is_the_project(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")
        (root / "My Song alt.als").write_bytes(b"other")

        result = adapter.validate_project(root)

        assert result.is_valid
        assert any("more than one" in w.lower() for w in result.warnings)


class TestInspect:
    def test_reports_the_primary_set_and_its_live_version(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")

        info = adapter.inspect_project(root)

        assert info.primary_set == root / "My Song.als"
        assert info.live_version == "11.0_11300"

    def test_survives_a_set_it_cannot_parse(self, adapter, tmp_path):
        root = make_project(tmp_path / "My Song")
        (root / "My Song.als").write_bytes(b"not gzip at all")

        info = adapter.inspect_project(root)

        assert info.primary_set == root / "My Song.als"
        assert info.live_version is None


class TestPrepare:
    def test_explains_collect_all_and_save_as_a_checklist(self, adapter, tmp_path):
        steps = adapter.prepare_project(make_project(tmp_path / "My Song"))

        assert steps
        assert any("Collect All and Save" in s for s in steps)
