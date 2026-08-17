import hashlib
import json
import unicodedata

from sessionsync.core.hashing import HashCache
from sessionsync.core.manifest import FileEntry, Manifest, build_file_map, diff_manifests


def entry(content: bytes, path: str = "x") -> FileEntry:
    return FileEntry(path=path, sha256=hashlib.sha256(content).hexdigest(), size=len(content))


def manifest_of(**files: FileEntry) -> Manifest:
    return Manifest(version=1, repository_id="r1", files={f.path: f for f in files.values()})


class TestBuildFileMap:
    def test_hashes_each_file_under_its_project_relative_path(self, tmp_path):
        (tmp_path / "Samples" / "Recorded").mkdir(parents=True)
        (tmp_path / "Song.als").write_bytes(b"als")
        (tmp_path / "Samples" / "Recorded" / "Vocal.wav").write_bytes(b"wav")

        files = build_file_map(
            tmp_path,
            [tmp_path / "Song.als", tmp_path / "Samples" / "Recorded" / "Vocal.wav"],
            HashCache(tmp_path / "cache.db"),
        )

        assert set(files) == {"Song.als", "Samples/Recorded/Vocal.wav"}
        assert files["Song.als"].sha256 == hashlib.sha256(b"als").hexdigest()
        assert files["Samples/Recorded/Vocal.wav"].size == 3

    def test_normalizes_decomposed_mac_filenames_to_nfc(self, tmp_path):
        decomposed = unicodedata.normalize("NFD", "Björk Take.wav")
        (tmp_path / decomposed).write_bytes(b"wav")

        files = build_file_map(tmp_path, [tmp_path / decomposed], HashCache(tmp_path / "c.db"))

        assert list(files) == [unicodedata.normalize("NFC", "Björk Take.wav")]


class TestManifestSerialisation:
    def test_round_trips_through_json(self):
        original = Manifest(
            version=24,
            repository_id="r1",
            parent_version=23,
            author={"name": "Bob", "email": "bob@example.com"},
            note="Guitar overdubs",
            files={"Song.als": entry(b"als", "Song.als")},
        )

        restored = Manifest.from_json(original.to_json())

        assert restored == original

    def test_serialises_to_stable_readable_json(self):
        m = Manifest(version=2, repository_id="r1", files={"b.wav": entry(b"b", "b.wav")})

        data = json.loads(m.to_json())

        assert data["version"] == 2
        assert data["project_format"] == "ableton"
        assert data["files"]["b.wav"]["sha256"] == hashlib.sha256(b"b").hexdigest()

    def test_two_manifests_with_same_content_serialise_identically(self):
        a = Manifest(version=1, repository_id="r", files={"a": entry(b"1", "a"), "b": entry(b"2", "b")})
        b = Manifest(version=1, repository_id="r", files={"b": entry(b"2", "b"), "a": entry(b"1", "a")})

        assert a.to_json() == b.to_json()


class TestDiff:
    def test_classifies_added_modified_deleted_and_unchanged(self):
        old = manifest_of(
            keep=entry(b"same", "Song.als"),
            change=entry(b"before", "Bass.wav"),
            gone=entry(b"old", "Scratch.wav"),
        )
        new = manifest_of(
            keep=entry(b"same", "Song.als"),
            change=entry(b"after!!", "Bass.wav"),
            fresh=entry(b"brand new take", "Guitar.wav"),
        )

        d = diff_manifests(old, new)

        assert d.added == ["Guitar.wav"]
        assert d.modified == ["Bass.wav"]
        assert d.deleted == ["Scratch.wav"]
        assert d.unchanged == ["Song.als"]

    def test_reports_bytes_that_would_need_uploading(self):
        old = manifest_of(keep=entry(b"same", "Song.als"))
        new = manifest_of(
            keep=entry(b"same", "Song.als"),
            fresh=entry(b"x" * 500, "Guitar.wav"),
        )

        d = diff_manifests(old, new)

        assert d.added_bytes == 500
        assert d.modified_bytes == 0
        assert d.changed_bytes == 500

    def test_a_file_moved_to_a_new_path_is_added_and_deleted_not_modified(self):
        old = manifest_of(a=entry(b"audio", "Samples/Take.wav"))
        new = manifest_of(a=entry(b"audio", "Samples/Take 1.wav"))

        d = diff_manifests(old, new)

        assert d.added == ["Samples/Take 1.wav"]
        assert d.deleted == ["Samples/Take.wav"]
        assert d.modified == []

    def test_no_changes_reports_empty_and_is_falsey(self):
        m = manifest_of(a=entry(b"same", "Song.als"))

        d = diff_manifests(m, m)

        assert not d.has_changes
        assert d.changed_bytes == 0
