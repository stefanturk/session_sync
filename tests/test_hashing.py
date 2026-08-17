import hashlib

import pytest

from sessionsync.core.hashing import HashCache, hash_file


def test_hash_file_matches_sha256_of_contents(tmp_path):
    f = tmp_path / "kick.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 5000)

    assert hash_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_hash_file_reads_in_chunks_without_loading_whole_file(tmp_path):
    f = tmp_path / "big.wav"
    f.write_bytes(b"x" * 300_000)

    reads = []
    real_open = type(f).open

    def spy_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        real_read = fh.read

        def counting_read(n=-1):
            data = real_read(n)
            reads.append(len(data))
            return data

        fh.read = counting_read
        return fh

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(f), "open", spy_open)
        digest = hash_file(f)

    assert digest == hashlib.sha256(f.read_bytes()).hexdigest()
    assert max(reads) <= 1024 * 1024


def test_hash_cache_returns_stored_digest_without_rereading(tmp_path):
    f = tmp_path / "vocal.wav"
    f.write_bytes(b"take one")
    cache = HashCache(tmp_path / "cache.db")

    first = cache.hash_file(f)
    f.chmod(0o000)  # unreadable: a cache miss would raise
    try:
        second = cache.hash_file(f)
    finally:
        f.chmod(0o644)

    assert first == second == hashlib.sha256(b"take one").hexdigest()


def test_hash_cache_rehashes_when_content_changes(tmp_path):
    f = tmp_path / "vocal.wav"
    f.write_bytes(b"take one")
    cache = HashCache(tmp_path / "cache.db")
    cache.hash_file(f)

    f.write_bytes(b"take two, much better")

    assert cache.hash_file(f) == hashlib.sha256(b"take two, much better").hexdigest()


def test_hash_cache_survives_reopening_the_database(tmp_path):
    f = tmp_path / "vocal.wav"
    f.write_bytes(b"take one")
    db = tmp_path / "cache.db"
    HashCache(db).hash_file(f)

    f.chmod(0o000)
    try:
        digest = HashCache(db).hash_file(f)
    finally:
        f.chmod(0o644)

    assert digest == hashlib.sha256(b"take one").hexdigest()
