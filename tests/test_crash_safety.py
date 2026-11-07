"""What happens when a push dies halfway.

The invariant under test: the current version of a repository never names a
file whose content is missing. A crashed push may leave unreferenced objects
behind — wasted space, never a broken project.
"""

import pytest

from sessionsync.core.errors import StalePushError
from sessionsync.core.repository import Repository
from tests.conftest import BOB, als_marked, live_set, record


class Boom(Exception):
    """Simulated power cut."""


class FailingStorage:
    """Passes calls through until the chosen call number, then dies."""

    def __init__(self, inner, fail_on: str, after: int = 0):
        self._inner = inner
        self._fail_on = fail_on
        self._remaining = after

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name != self._fail_on:
            return attr

        def guarded(*args, **kwargs):
            if self._remaining <= 0:
                raise Boom(f"connection lost during {name}")
            self._remaining -= 1
            return attr(*args, **kwargs)

        return guarded


def assert_repository_is_intact(storage):
    """Every published version must be fully downloadable."""
    from sessionsync.core.manifest import Manifest

    for version in storage.list_versions():
        manifest = Manifest.from_json(storage.read_manifest(version))
        missing = manifest.hashes() - storage.existing_objects(manifest.hashes())
        assert not missing, f"version {version} references missing content: {missing}"


@pytest.fixture
def big_change(project):
    for i in range(4):
        record(project, f"Samples/Recorded/Take {i}.wav", f"take {i}".encode())
    return project


class TestPushInterrupted:
    @pytest.mark.parametrize("uploads_completed", [0, 1, 2, 3])
    def test_dying_while_uploading_leaves_the_project_openable(
        self, alice, remote, big_change, uploads_completed
    ):
        alice.storage = FailingStorage(remote, "upload_object", after=uploads_completed)

        with pytest.raises(Boom):
            alice.push(note="Takes")

        assert remote.current_version() == 1
        assert_repository_is_intact(remote)

    def test_dying_before_the_manifest_is_published_keeps_the_old_version(
        self, alice, remote, big_change
    ):
        alice.storage = FailingStorage(remote, "publish_manifest")

        with pytest.raises(Boom):
            alice.push(note="Takes")

        assert remote.current_version() == 1
        assert_repository_is_intact(remote)

    def test_a_crashed_push_can_simply_be_repeated(self, alice, remote, big_change):
        alice.storage = FailingStorage(remote, "upload_object", after=2)
        with pytest.raises(Boom):
            alice.push(note="Takes")

        alice.storage = remote
        result = alice.push(note="Takes")

        assert result.version == 2
        assert_repository_is_intact(remote)

    def test_a_crashed_push_does_not_advance_this_computers_version(
        self, alice, remote, big_change
    ):
        alice.storage = FailingStorage(remote, "publish_manifest")

        with pytest.raises(Boom):
            alice.push(note="Takes")

        assert alice.base_version == 1

    def test_another_person_can_still_pull_after_someone_elses_push_died(
        self, alice, remote, big_change, tmp_path
    ):
        alice.storage = FailingStorage(remote, "publish_manifest")
        with pytest.raises(Boom):
            alice.push(note="Takes")

        clone = Repository.clone(remote, tmp_path / "carol" / "My Song", author=BOB)

        assert clone.base_version == 1
        assert (clone.project_root / "My Song.als").exists()


class TestPullInterrupted:
    def test_a_failed_download_never_leaves_a_half_written_audio_file(
        self, alice, bob, remote, project
    ):
        record(project, "Samples/Recorded/Guitar.wav", b"guitar" * 1000)
        alice.push(note="Guitar")
        bob.storage = FailingStorage(remote, "download_object")

        with pytest.raises(Boom):
            bob.pull()

        assert not (bob.project_root / "Samples" / "Recorded" / "Guitar.wav").exists()

    def test_replaced_files_survive_a_failed_pull_in_the_local_trash(
        self, alice, bob, remote, project
    ):
        (project / "My Song.als").write_bytes(als_marked(b"alice new arrangement"))
        alice.push(note="Arrangement")
        bob.storage = FailingStorage(remote, "download_object")

        with pytest.raises(Boom):
            bob.pull()

        rescued = list(bob.state.trash_dir.rglob("My Song.als"))
        assert rescued, "the file being replaced must still be recoverable"

    def test_a_failed_pull_does_not_pretend_the_version_arrived(
        self, alice, bob, remote, project
    ):
        (project / "My Song.als").write_bytes(als_marked(b"alice edit"))
        alice.push(note="Alice")
        bob.storage = FailingStorage(remote, "download_object")

        with pytest.raises(Boom):
            bob.pull()

        assert bob.base_version == 1


class TestSimultaneousPush:
    def test_a_push_that_lands_mid_flight_still_blocks_the_slower_one(
        self, alice, bob, remote, project
    ):
        """Bob checks the version, then Alice publishes before Bob's manifest lands."""
        record(bob.project_root, "Samples/Recorded/Bass.wav", b"bob bass")
        record(project, "Samples/Recorded/Guitar.wav", b"alice guitar")

        original_existing = remote.existing_objects
        fired = []

        def alice_sneaks_in(hashes):
            if not fired:
                fired.append(True)
                alice.push(note="Alice guitar")  # lands as v2 while Bob is uploading
            return original_existing(hashes)

        bob.storage = remote
        remote.existing_objects = alice_sneaks_in
        try:
            with pytest.raises(StalePushError):
                bob.push(note="Bob bass")
        finally:
            remote.existing_objects = original_existing

        assert remote.current_version() == 2
        assert alice.read_manifest(2).note == "Alice guitar"
        assert_repository_is_intact(remote)

    def test_the_loser_keeps_their_recordings_and_can_push_after_pulling(
        self, alice, bob, remote, project
    ):
        # Bob's idea lives in its own Live Set, so pulling Alice's Set does not touch it
        (bob.project_root / "Bob Sketch.als").write_bytes(
            live_set(samples=["Samples/Recorded/Bass.wav"])
        )
        (bob.project_root / "Samples" / "Recorded" / "Bass.wav").write_bytes(b"bob bass")
        record(project, "Samples/Recorded/Guitar.wav", b"alice guitar")
        alice.push(note="Alice guitar")

        with pytest.raises(StalePushError):
            bob.push(note="Bob bass")
        bob.pull()
        result = bob.push(note="Bob bass")

        assert result.version == 3
        assert (bob.project_root / "Samples" / "Recorded" / "Bass.wav").read_bytes() == b"bob bass"
        assert set(bob.read_manifest(3).files) >= {
            "Samples/Recorded/Bass.wav",
            "Samples/Recorded/Guitar.wav",
        }
        assert_repository_is_intact(remote)
