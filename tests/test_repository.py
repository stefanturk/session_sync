import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.errors import NothingToPushError, NotAProjectError, StalePushError
from sessionsync.core.repository import Repository
from tests.conftest import ALICE, BOB, write_project, record, als_marked


class TestCreate:
    def test_registers_the_project_with_the_remote(self, project, remote):
        Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)

        metadata = remote.read_repo_metadata()
        assert metadata["project_name"] == "My Song"
        assert metadata["project_format"] == "ableton"

    def test_creating_does_not_publish_a_version_on_its_own(self, project, remote):
        Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)

        assert remote.current_version() == 0

    def test_refuses_a_folder_that_is_not_an_ableton_project(self, tmp_path, remote):
        plain = tmp_path / "Documents"
        plain.mkdir()

        with pytest.raises(NotAProjectError):
            Repository.create(plain, remote, adapter=AbletonAdapter(), author=ALICE)

    def test_leaves_the_project_folder_openable_by_ableton(self, project, remote):
        Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)

        assert (project / "My Song.als").exists()
        assert (project / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"vocal audio"


class TestPush:
    def test_first_push_becomes_version_one(self, project, remote):
        repo = Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)

        result = repo.push(note="Initial version")

        assert result.version == 1
        assert remote.current_version() == 1

    def test_pushing_uploads_every_tracked_file_as_an_object(self, alice, remote):
        manifest = alice.read_manifest(1)

        assert set(manifest.files) == {
            "My Song.als",
            "Ableton Project Info/Project11.cfg",
            "Samples/Recorded/Vocal.wav",
        }
        assert remote.existing_objects(manifest.hashes()) == manifest.hashes()

    def test_records_who_pushed_and_why(self, alice):
        manifest = alice.read_manifest(1)

        assert manifest.author == ALICE
        assert manifest.note == "Initial version"

    def test_a_second_push_only_uploads_what_changed(self, alice, project):
        record(project, "Samples/Recorded/Guitar.wav", b"g" * 5000)

        result = alice.push(note="Guitar")

        assert result.version == 2
        assert result.uploaded_objects == 2  # the take, and the Live Set that now uses it
        assert result.uploaded_bytes == 5000 + (project / "My Song.als").stat().st_size

    def test_unchanged_audio_is_not_uploaded_again(self, alice, project, remote):
        (project / "My Song.als").write_bytes(als_marked(b"edited set"))

        result = alice.push(note="Arrangement tweak")

        assert result.diff.modified == ["My Song.als"]
        assert result.diff.unchanged == [
            "Ableton Project Info/Project11.cfg",
            "Samples/Recorded/Vocal.wav",
        ]
        assert result.uploaded_objects == 1

    def test_pushing_with_nothing_changed_is_refused(self, alice):
        with pytest.raises(NothingToPushError):
            alice.push(note="nothing happened")

    def test_deleting_a_file_locally_removes_it_from_the_next_version(self, alice, project):
        (project / "Samples" / "Recorded" / "Vocal.wav").unlink()

        result = alice.push(note="Dropped the vocal")

        assert result.diff.deleted == ["Samples/Recorded/Vocal.wav"]
        assert "Samples/Recorded/Vocal.wav" not in alice.read_manifest(2).files

    def test_the_old_version_still_lists_the_deleted_file(self, alice, project):
        (project / "Samples" / "Recorded" / "Vocal.wav").unlink()
        alice.push(note="Dropped the vocal")

        assert "Samples/Recorded/Vocal.wav" in alice.read_manifest(1).files

    def test_pushing_advances_this_computers_base_version(self, alice, project):
        record(project, "Samples/Recorded/Guitar.wav", b"g")

        alice.push(note="Guitar")

        assert alice.base_version == 2


class TestClone:
    def test_reconstructs_the_project_on_another_computer(self, bob):
        assert (bob.project_root / "My Song.als").exists()
        assert (bob.project_root / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"vocal audio"
        assert (bob.project_root / "Ableton Project Info" / "Project11.cfg").exists()

    def test_the_clone_knows_which_version_it_is_on(self, bob):
        assert bob.base_version == 1

    def test_the_clone_can_be_reopened_later(self, bob):
        reopened = Repository.open(bob.project_root, author=BOB)

        assert reopened.base_version == 1
        assert not reopened.status().has_local_changes


class TestPull:
    def test_brings_in_another_persons_recordings(self, alice, bob, project):
        record(project, "Samples/Recorded/Guitar.wav", b"guitar take")
        alice.push(note="Guitar")

        result = bob.pull()

        assert result.to_version == 2
        assert (bob.project_root / "Samples" / "Recorded" / "Guitar.wav").read_bytes() == b"guitar take"

    def test_pulling_when_already_current_changes_nothing(self, bob):
        result = bob.pull()

        assert result.to_version == 1
        assert not result.diff.has_changes

    def test_a_file_the_new_version_no_longer_uses_stays_on_bobs_computer(
        self, alice, bob, project
    ):
        (project / "My Song.als").write_bytes(als_marked(b"no vocal", samples=()))
        alice.push(note="Dropped the vocal")

        bob.pull()

        # an older revision still uses it, so it is never removed
        assert (bob.project_root / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"vocal audio"
        assert "Samples/Recorded/Vocal.wav" not in bob.read_manifest(2).files

    def test_a_file_someone_else_changed_is_kept_in_the_trash_before_being_replaced(
        self, alice, bob, project
    ):
        (project / "My Song.als").write_bytes(als_marked(b"alices new arrangement"))
        alice.push(note="Arrangement")

        bob.pull()

        assert (bob.project_root / "My Song.als").read_bytes() == als_marked(b"alices new arrangement")
        assert list(bob.state.trash_dir.rglob("My Song.als"))

    def test_untracked_files_bob_added_himself_are_left_alone(self, alice, bob, project):
        (bob.project_root / "notes.txt").write_text("my ideas")
        record(project, "Samples/Recorded/Guitar.wav", b"guitar")
        alice.push(note="Guitar")

        bob.pull()

        assert (bob.project_root / "notes.txt").read_text() == "my ideas"


class TestStalePush:
    def test_the_second_person_to_push_from_the_same_version_is_stopped(
        self, alice, bob, project
    ):
        record(project, "Samples/Recorded/Guitar.wav", b"alice guitar")
        alice.push(note="Alice guitar")
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")

        with pytest.raises(StalePushError) as excinfo:
            bob.push(note="Bob bass")

        assert "Pull" in str(excinfo.value)

    def test_a_blocked_push_leaves_the_remote_untouched(self, alice, bob, project):
        record(project, "Samples/Recorded/Guitar.wav", b"alice guitar")
        alice.push(note="Alice guitar")
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")

        with pytest.raises(StalePushError):
            bob.push(note="Bob bass")

        assert bob.remote_version() == 2
        assert alice.read_manifest(2).author == ALICE

    def test_a_blocked_push_leaves_bobs_own_recordings_in_place(self, alice, bob, project):
        (project / "My Song.als").write_bytes(als_marked(b"alice edit"))
        alice.push(note="Alice")
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")

        with pytest.raises(StalePushError):
            bob.push(note="Bob bass")

        assert (bob.project_root / "Samples" / "Recorded" / "Bass.wav").read_bytes() == b"bob bass"

    def test_after_pulling_bob_can_push_his_work(self, alice, bob, project):
        (project / "My Song.als").write_bytes(als_marked(b"alice edit"))
        alice.push(note="Alice")
        bob.pull()
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")
        result = bob.push(note="Bob bass")

        assert result.version == 3
        assert "Samples/Recorded/Bass.wav" in bob.read_manifest(3).files


class TestStatus:
    def test_a_freshly_pushed_project_is_up_to_date(self, alice):
        status = alice.status()

        assert status.state == "up_to_date"
        assert status.local_version == status.remote_version == 1
        assert not status.has_local_changes

    def test_reports_local_work_that_has_not_been_pushed(self, alice, project):
        record(project, "Samples/Recorded/Guitar.wav", b"guitar")

        status = alice.status()

        assert status.state == "local_changes"
        assert status.diff.added == ["Samples/Recorded/Guitar.wav"]

    def test_tells_the_other_person_they_are_behind(self, alice, bob, project):
        (project / "My Song.als").write_bytes(als_marked(b"alice edit"))
        alice.push(note="Alice")

        status = bob.status()

        assert status.state == "behind"
        assert status.versions_behind == 1

    def test_being_behind_with_local_work_is_the_blocked_state(self, alice, bob, project):
        (project / "My Song.als").write_bytes(als_marked(b"alice edit"))
        alice.push(note="Alice")
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")

        status = bob.status()

        assert status.state == "push_blocked"
        assert status.versions_behind == 1
        assert status.has_local_changes

    def test_predicts_what_a_push_would_upload(self, alice, project):
        record(project, "Samples/Recorded/Guitar.wav", b"g" * 4000)

        status = alice.status()

        new = 4000 + (project / "My Song.als").stat().st_size  # the take and the Set using it
        assert status.upload_bytes == new
        assert status.already_stored_bytes == status.project_bytes - new


class TestHistory:
    def test_lists_previous_sessions_newest_first(self, alice, project):
        (project / "My Song.als").write_bytes(als_marked(b"v2"))
        alice.push(note="Guitar overdubs")

        history = alice.history()

        assert [h.version for h in history] == [2, 1]
        assert history[0].note == "Guitar overdubs"
        assert history[0].author["name"] == "Alice"

    def test_each_entry_summarises_what_changed(self, alice, project):
        set_before = (project / "My Song.als").stat().st_size
        record(project, "Samples/Recorded/Guitar.wav", b"g" * 100)
        set_growth = (project / "My Song.als").stat().st_size - set_before
        alice.push(note="Guitar")

        latest = alice.history()[0]

        assert latest.added == ["Samples/Recorded/Guitar.wav"]
        assert latest.size_change == 100 + set_growth


class TestReconstructionFidelity:
    def test_a_pushed_project_comes_back_identically_on_another_machine(
        self, tmp_path, remote, project
    ):
        repo = Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)
        repo.push(note="v1")

        clone = Repository.clone(remote, tmp_path / "carol" / "My Song", author=BOB)

        original = {
            p.relative_to(project).as_posix(): p.read_bytes()
            for p in AbletonAdapter().enumerate_tracked_files(project)
        }
        restored = {
            p.relative_to(clone.project_root).as_posix(): p.read_bytes()
            for p in AbletonAdapter().enumerate_tracked_files(clone.project_root)
        }
        assert restored == original

    def test_a_project_with_accented_filenames_round_trips(self, tmp_path, remote):
        project = write_project(tmp_path / "alice" / "My Song")
        record(project, "Samples/Recorded/Björk Take.wav", b"take")
        repo = Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)
        repo.push(note="v1")

        clone = Repository.clone(remote, tmp_path / "bob" / "My Song", author=BOB)

        names = [p.name for p in (clone.project_root / "Samples" / "Recorded").iterdir()]
        assert any(n.startswith("Bj") and n.endswith("Take.wav") for n in names)
