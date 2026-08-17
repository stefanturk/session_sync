import pytest

from sessionsync.storage.local_folder import LocalFolderStorage
from tests.storage_contract import StorageContract


class TestLocalFolderStorage(StorageContract):
    @pytest.fixture
    def storage(self, tmp_path):
        return LocalFolderStorage(tmp_path / "remote" / "My Song")


class TestLocalFolderLayout:
    def test_objects_are_stored_content_addressed_in_sharded_folders(self, tmp_path):
        storage = LocalFolderStorage(tmp_path / "remote")
        src = tmp_path / "a.wav"
        src.write_bytes(b"audio")

        storage.upload_object("abcdef" + "0" * 58, src)

        assert (tmp_path / "remote" / "objects" / "ab" / ("abcdef" + "0" * 58)).exists()

    def test_manifests_are_named_by_zero_padded_version(self, tmp_path):
        storage = LocalFolderStorage(tmp_path / "remote")

        storage.publish_manifest(24, b"{}")

        assert (tmp_path / "remote" / "manifests" / "v000024.json").read_bytes() == b"{}"

    def test_an_interrupted_object_upload_leaves_no_readable_object(self, tmp_path):
        storage = LocalFolderStorage(tmp_path / "remote")
        src = tmp_path / "a.wav"
        src.write_bytes(b"audio")

        def explode(*args, **kwargs):
            raise OSError("disk full")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("shutil.copyfile", explode)
            with pytest.raises(OSError):
                storage.upload_object("cc" * 32, src)

        assert storage.existing_objects({"cc" * 32}) == set()
