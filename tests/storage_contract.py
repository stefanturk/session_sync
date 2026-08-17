"""Rules every StorageProvider must obey.

Subclass with a `storage` fixture. LocalFolderStorage passes this today;
GoogleDriveStorage must pass exactly the same suite before it can be trusted.
"""

import pytest

from sessionsync.storage.base import VersionAlreadyExistsError


class StorageContract:
    @pytest.fixture
    def storage(self):
        raise NotImplementedError

    # --- metadata ------------------------------------------------------

    def test_repo_metadata_round_trips(self, storage):
        storage.write_repo_metadata({"repository_id": "r1", "project_name": "My Song"})

        assert storage.read_repo_metadata()["project_name"] == "My Song"

    # --- objects -------------------------------------------------------

    def test_uploaded_object_can_be_downloaded_byte_for_byte(self, storage, tmp_path):
        src = tmp_path / "vocal.wav"
        src.write_bytes(b"audio" * 10_000)
        storage.upload_object("aa" * 32, src)

        dest = tmp_path / "out" / "vocal.wav"
        storage.download_object("aa" * 32, dest)

        assert dest.read_bytes() == src.read_bytes()

    def test_reports_which_of_many_objects_are_already_stored(self, storage, tmp_path):
        src = tmp_path / "a"
        src.write_bytes(b"a")
        storage.upload_object("11" * 32, src)

        present = storage.existing_objects({"11" * 32, "22" * 32})

        assert present == {"11" * 32}

    def test_uploading_the_same_hash_twice_is_harmless(self, storage, tmp_path):
        src = tmp_path / "a"
        src.write_bytes(b"same content")
        storage.upload_object("33" * 32, src)
        storage.upload_object("33" * 32, src)

        dest = tmp_path / "out"
        storage.download_object("33" * 32, dest)
        assert dest.read_bytes() == b"same content"

    def test_downloading_an_object_that_was_never_uploaded_raises(self, storage, tmp_path):
        with pytest.raises(FileNotFoundError):
            storage.download_object("ff" * 32, tmp_path / "nope")

    # --- manifests / HEAD ----------------------------------------------

    def test_a_fresh_repository_has_no_current_version(self, storage):
        assert storage.current_version() == 0

    def test_current_version_is_the_highest_published_manifest(self, storage):
        storage.publish_manifest(1, b'{"version": 1}')
        storage.publish_manifest(2, b'{"version": 2}')

        assert storage.current_version() == 2

    def test_published_manifests_can_be_read_back(self, storage):
        storage.publish_manifest(1, b'{"version": 1}')

        assert storage.read_manifest(1) == b'{"version": 1}'

    def test_publishing_a_version_that_already_exists_is_refused(self, storage):
        storage.publish_manifest(1, b'{"version": 1, "author": "alice"}')

        with pytest.raises(VersionAlreadyExistsError):
            storage.publish_manifest(1, b'{"version": 1, "author": "bob"}')

    def test_the_first_publisher_of_a_version_keeps_it(self, storage):
        storage.publish_manifest(1, b'{"author": "alice"}')

        with pytest.raises(VersionAlreadyExistsError):
            storage.publish_manifest(1, b'{"author": "bob"}')

        assert storage.read_manifest(1) == b'{"author": "alice"}'

    def test_lists_every_published_version_in_order(self, storage):
        for v in (1, 2, 3):
            storage.publish_manifest(v, b"{}")

        assert storage.list_versions() == [1, 2, 3]

    # --- session presence ----------------------------------------------

    def test_session_heartbeats_are_visible_to_everyone(self, storage):
        storage.write_session("bob-a1b2", {"user": "bob", "state": "working"})

        assert storage.list_sessions() == [{"user": "bob", "state": "working"}]

    def test_refreshing_a_heartbeat_replaces_it_rather_than_adding_one(self, storage):
        storage.write_session("bob-a1b2", {"user": "bob", "beat": 1})
        storage.write_session("bob-a1b2", {"user": "bob", "beat": 2})

        assert storage.list_sessions() == [{"user": "bob", "beat": 2}]

    def test_ending_a_session_removes_the_heartbeat(self, storage):
        storage.write_session("bob-a1b2", {"user": "bob"})
        storage.delete_session("bob-a1b2")

        assert storage.list_sessions() == []

    def test_deleting_an_already_gone_session_is_not_an_error(self, storage):
        storage.delete_session("never-existed")
