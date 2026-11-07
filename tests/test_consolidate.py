"""Consolidate: Push sends only what the Live Sets use, and makes it self-contained."""

import gzip

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.adapters.ableton.consolidate import ConsolidateError
from sessionsync.core.hashing import hash_file
from sessionsync.core.repository import Repository
from sessionsync.storage.master_folder import MasterFolderStorage

from tests.conftest import ALICE, BOB, VOCAL, live_set, record, write_project


def ref(relative, absolute, *, ref_type="3", parent="SampleRef", pack="", pack_id=""):
    return (
        f"<{parent}><FileRef>"
        f'<RelativePathType Value="{ref_type}" />'
        f'<RelativePath Value="{relative}" />'
        f'<Path Value="{absolute}" />'
        f'<Type Value="2" /><LivePackName Value="{pack}" /><LivePackId Value="{pack_id}" />'
        '<OriginalFileSize Value="0" />'
        f"</FileRef></{parent}>"
    )


def set_with(*refs, version="11.0_11300", extra="") -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Ableton MajorVersion="5" MinorVersion="{version}" Creator="Ableton Live 11.3">'
        f"<LiveSet>{extra}<Tracks>{''.join(refs)}</Tracks></LiveSet></Ableton>"
    )
    return gzip.compress(xml.encode("utf-8"), mtime=0)


def plan(project, *, rewrite=True, known=None):
    return AbletonAdapter().plan_push(project, known=known, hash_file=hash_file, rewrite=rewrite)


def refs_in(data: bytes) -> list[tuple[str, str]]:
    import re

    xml = gzip.decompress(data).decode()
    return re.findall(r'RelativePathType Value="(\d)" /><RelativePath Value="([^"]*)"', xml)


@pytest.fixture
def song(tmp_path):
    return write_project(tmp_path / "alice" / "My Song")


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "Room Mic.wav").write_bytes(b"room mic audio")
    return folder


class TestOnlyWhatTheSetsUse:
    def test_audio_the_set_uses_is_sent_and_unused_audio_stays_here(self, song):
        (song / "Samples" / "Recorded" / "Old Take.wav").write_bytes(b"x" * 1000)

        p = plan(song)

        assert VOCAL in p.files
        assert "Samples/Recorded/Old Take.wav" not in p.files
        assert p.unused == ["Samples/Recorded/Old Take.wav"]
        assert p.unused_bytes == 1000

    def test_small_non_audio_files_still_travel(self, song):
        (song / "Lyrics.txt").write_text("la la")

        assert "Lyrics.txt" in plan(song).files
        assert "Ableton Project Info/Project11.cfg" in plan(song).files

    def test_every_set_in_the_project_counts(self, song):
        (song / "Samples" / "Recorded" / "Idea.wav").write_bytes(b"idea")
        (song / "Sketch.als").write_bytes(live_set(samples=["Samples/Recorded/Idea.wav"]))

        p = plan(song)

        assert {"Sketch.als", "Samples/Recorded/Idea.wav", VOCAL} <= set(p.files)

    def test_preset_history_is_not_a_dependency_but_max_devices_are(self, song):
        (song / "Presets").mkdir()
        (song / "Presets" / "Lead.adv").write_bytes(b"preset")
        (song / "Devices").mkdir()
        (song / "Devices" / "Glitch.amxd").write_bytes(b"max device")
        (song / "My Song.als").write_bytes(set_with(
            ref(VOCAL, "/x/" + VOCAL),
            ref("Presets/Lead.adv", "/x/Lead.adv", parent="PresetRef"),
            ref("Devices/Glitch.amxd", "/x/Glitch.amxd", parent="MxPatchRef"),
        ))

        p = plan(song)

        assert "Devices/Glitch.amxd" in p.files
        assert "Devices/Glitch.amxd" not in p.unused
        assert "Presets/Lead.adv" in p.files  # small non-audio: travels anyway, never "missing"
        assert p.missing == []

    def test_a_sample_that_is_nowhere_is_reported_missing_and_not_sent(self, song):
        (song / "My Song.als").write_bytes(set_with(
            ref(VOCAL, "/x/" + VOCAL), ref("Samples/Gone.wav", "/nowhere/Gone.wav")
        ))

        p = plan(song)

        assert p.missing == ["Samples/Gone.wav"]
        assert not p.problems  # missing audio is a warning, not a reason to stop

    def test_past_revisions_are_never_mistaken_for_working_sets(self, song):
        (song / "Past Revisions").mkdir()
        (song / "Past Revisions" / "Rev 0001.als").write_bytes(b"not even readable")

        p = plan(song)

        assert not p.problems
        assert not any(f.startswith("Past Revisions/") for f in p.files)


class TestPacks:
    def test_ableton_pack_samples_are_skipped_and_named(self, song):
        (song / "My Song.als").write_bytes(set_with(
            ref(VOCAL, "/x/" + VOCAL),
            ref("Samples/Kick.aif", "/Applications/Ableton Live 11 Standard.app/Contents/"
                "App-Resources/Core Library/Samples/Kick.aif",
                ref_type="5", pack="Core Library", pack_id="www.ableton.com/0"),
        ))

        p = plan(song)

        assert p.packs == ["Core Library"]
        assert p.missing == []
        assert not any("Kick" in f for f in p.files)

    def test_samples_from_someone_elses_pack_are_copied_in(self, song, tmp_path):
        pack = tmp_path / "Third Party Pack" / "Samples"
        pack.mkdir(parents=True)
        (pack / "Snare.wav").write_bytes(b"snare")
        (song / "My Song.als").write_bytes(set_with(
            ref(VOCAL, "/x/" + VOCAL),
            ref("Samples/Snare.wav", str(pack / "Snare.wav"), ref_type="6",
                pack="Cool Drums", pack_id="coolsamples.com/7"),
        ))

        p = plan(song)

        assert p.files["Samples/Imported/Snare.wav"] == pack / "Snare.wav"
        assert p.packs == []


class TestOutsideFiles:
    def outside_set(self, song, library, *, ref_type="6"):
        (song / "My Song.als").write_bytes(set_with(
            ref(VOCAL, "/x/" + VOCAL),
            ref("Room Mic.wav", str(library / "Room Mic.wav"), ref_type=ref_type),
        ))

    def test_they_are_copied_in_under_samples_imported(self, song, library):
        self.outside_set(song, library)

        p = plan(song)

        assert p.imported == {"Samples/Imported/Room Mic.wav": library / "Room Mic.wav"}
        assert p.files["Samples/Imported/Room Mic.wav"] == library / "Room Mic.wav"

    def test_only_the_shared_copy_of_the_set_is_rewritten(self, song, library):
        self.outside_set(song, library)
        before = (song / "My Song.als").read_bytes()

        p = plan(song)

        assert (song / "My Song.als").read_bytes() == before  # never touched
        assert refs_in(p.rewritten["My Song.als"]) == [
            ("3", VOCAL), ("3", "Samples/Imported/Room Mic.wav"),
        ]

    def test_the_rewrite_changes_nothing_outside_the_references(self, song, library):
        (song / "My Song.als").write_bytes(set_with(
            ref("Room Mic.wav", str(library / "Room Mic.wav"), ref_type="6"),
            extra='<Annotation Value="keep me &amp; this exactly" />',
        ))
        original = gzip.decompress((song / "My Song.als").read_bytes())

        rewritten = gzip.decompress(plan(song).rewritten["My Song.als"])

        start, end = original.index(b"<FileRef>"), original.index(b"</FileRef>")
        assert rewritten[:start] == original[:start]
        assert rewritten.endswith(original[end:])

    def test_a_set_that_needs_nothing_changed_is_sent_as_it_is(self, song):
        assert plan(song).rewritten == {}

    def test_a_name_already_taken_by_a_different_file_gets_a_number(self, song, library):
        self.outside_set(song, library)
        known = {"Samples/Imported/Room Mic.wav": "someone-elses-hash"}

        p = plan(song, known=known)

        assert "Samples/Imported/Room Mic (2).wav" in p.imported

    def test_the_same_file_keeps_the_name_it_was_given_before(self, song, library):
        self.outside_set(song, library)
        sha = hash_file(library / "Room Mic.wav")
        known = {"Samples/Imported/Room Mic (2).wav": sha, "Samples/Imported/Room Mic.wav": "other"}

        assert list(plan(song, known=known).imported) == ["Samples/Imported/Room Mic (2).wav"]

    def test_set_relative_paths_inside_the_project_become_project_relative(self, song):
        # Live 11 sometimes records a path relative to the Set itself; a copy of
        # the Set inside Past Revisions/ would then look in the wrong place.
        (song / "My Song.als").write_bytes(set_with(ref(VOCAL, "", ref_type="1")))

        assert refs_in(plan(song).rewritten["My Song.als"]) == [("3", VOCAL)]


class TestStoppingSafely:
    def test_an_unreadable_set_stops_push_and_says_which(self, song):
        (song / "My Song.als").write_bytes(b"this is not a Live Set")

        p = plan(song)

        with pytest.raises(ConsolidateError, match="My Song.als could not be read"):
            p.check()

    def test_a_live_10_main_set_asks_for_a_resave_in_live_11(self, song):
        (song / "My Song.als").write_bytes(set_with(version="10.0_377"))

        with pytest.raises(ConsolidateError, match="Live 11 or newer"):
            plan(song).check()

    def test_an_old_live_10_side_set_is_left_out_not_fatal(self, song):
        (song / "Old Idea.als").write_bytes(set_with(version="10.0_377"))

        p = plan(song)

        assert not p.problems
        assert p.skipped_sets == ["Old Idea.als"]

    def test_a_rewrite_that_does_not_read_back_stops_push(self, song, library, monkeypatch):
        from sessionsync.adapters.ableton import consolidate

        (song / "My Song.als").write_bytes(set_with(
            ref("Room Mic.wav", str(library / "Room Mic.wav"), ref_type="6"),
        ))
        monkeypatch.setattr(consolidate, "_set_value", lambda chunk, tag, value: chunk + b"<broken")

        p = plan(song)

        assert p.problems and "Could not safely prepare My Song.als" in p.problems[0]
        assert p.rewritten == {}


# --- through a real push --------------------------------------------------------


@pytest.fixture
def master(tmp_path):
    return MasterFolderStorage(tmp_path / "drive" / "My Song")


def test_push_sends_only_used_files_and_a_friend_can_open_the_result(tmp_path, master, library):
    song = write_project(tmp_path / "alice" / "My Song")
    (song / "Samples" / "Recorded" / "Old Take.wav").write_bytes(b"x" * 5000)
    (song / "My Song.als").write_bytes(set_with(
        ref(VOCAL, str(song / VOCAL)),
        ref("Room Mic.wav", str(library / "Room Mic.wav"), ref_type="6"),
    ))
    alice = Repository.create(song, master, adapter=AbletonAdapter(), author=ALICE)
    alice.push(note="first")

    assert not (master.root / "Samples/Recorded/Old Take.wav").exists()
    assert (master.root / "Samples/Imported/Room Mic.wav").read_bytes() == b"room mic audio"
    assert refs_in((master.root / "My Song.als").read_bytes())[1] == ("3", "Samples/Imported/Room Mic.wav")

    # Alice's own copy is not "changed" just because the shared Set was rewritten
    assert alice.status().state == "up_to_date"

    bob = Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)
    assert (bob.project_root / "Samples/Imported/Room Mic.wav").read_bytes() == b"room mic audio"
    assert bob.status().state == "up_to_date"


def test_the_person_who_pushed_does_not_download_their_own_set_again(tmp_path, master, library):
    song = write_project(tmp_path / "alice" / "My Song")
    (song / "My Song.als").write_bytes(set_with(
        ref("Room Mic.wav", str(library / "Room Mic.wav"), ref_type="6"),
    ))
    alice = Repository.create(song, master, adapter=AbletonAdapter(), author=ALICE)
    alice.push(note="first")
    bob = Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)
    record(bob.project_root, "Samples/Recorded/Bass.wav", b"bass")
    bob.push(note="bass")
    mine = (song / "My Song.als").read_bytes()

    with pytest.raises(Exception):
        alice.push()  # behind: must pull first
    alice.pull()

    assert (song / "My Song.als").read_bytes() != mine  # Bob changed it, so it came down
    assert alice.status().state == "up_to_date"


def test_a_failing_consolidate_uploads_nothing(tmp_path, master):
    song = write_project(tmp_path / "alice" / "My Song")
    alice = Repository.create(song, master, adapter=AbletonAdapter(), author=ALICE)
    alice.push(note="first")
    (song / "My Song.als").write_bytes(b"corrupt")

    with pytest.raises(ConsolidateError):
        alice.push(note="broken")

    assert master.current_version() == 1
    assert not (master.meta / "staging").exists() or not any((master.meta / "staging").rglob("*"))


class TestOpeningAnOldRevision:
    @pytest.fixture
    def two_revisions(self, tmp_path, master):
        from tests.conftest import als_marked

        song = write_project(tmp_path / "alice" / "My Song")
        alice = Repository.create(song, master, adapter=AbletonAdapter(), author=ALICE)
        alice.push(note="with vocal")
        (song / "My Song.als").write_bytes(als_marked("no vocal", samples=()))
        alice.push(note="no vocal")
        bob = Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)
        return alice, bob

    def test_pull_brings_the_current_revision_but_not_old_only_samples(self, two_revisions):
        _, bob = two_revisions

        assert not (bob.project_root / VOCAL).exists()
        assert len(list((bob.project_root / "Past Revisions").glob("*.als"))) == 2

    def test_opening_rev_1_fetches_what_it_needs_first(self, two_revisions):
        _, bob = two_revisions

        opened = bob.open_revision(1)

        assert opened.parent.name == "Past Revisions" and opened.name.startswith("Rev 0001")
        assert (bob.project_root / VOCAL).read_bytes() == b"vocal audio"

    def test_a_conflicting_file_opens_the_revision_as_a_separate_copy(self, two_revisions):
        _, bob = two_revisions
        (bob.project_root / VOCAL).parent.mkdir(parents=True, exist_ok=True)
        (bob.project_root / VOCAL).write_bytes(b"bob's own vocal")

        opened = bob.open_revision(1)

        assert "(Rev 1)" in opened.parent.name
        assert (opened.parent / VOCAL).read_bytes() == b"vocal audio"
        assert (bob.project_root / VOCAL).read_bytes() == b"bob's own vocal"  # untouched


def test_an_empty_ableton_project_info_folder_still_exists_on_both_sides(tmp_path, master):
    # Live locates a project, and so a Past Revision's samples, by this folder
    song = write_project(tmp_path / "alice" / "My Song")
    (song / "Ableton Project Info" / "Project11.cfg").unlink()
    alice = Repository.create(song, master, adapter=AbletonAdapter(), author=ALICE)
    alice.push(note="first")
    bob = Repository.clone(master, tmp_path / "bob" / "My Song", author=BOB)

    assert (master.root / "Ableton Project Info").is_dir()
    assert (bob.project_root / "Ableton Project Info").is_dir()
