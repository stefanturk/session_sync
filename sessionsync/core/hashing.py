"""Streaming content hashing with an on-disk cache.

A 6 GB Ableton project would take minutes to rehash on every status check, so
digests are cached against the file's identity (size, mtime, inode). Any change
to those invalidates the entry and the file is read again.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def hash_file(path: Path) -> str:
    """SHA-256 of a file's contents, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


class HashCache:
    """Maps (path, size, mtime, inode) to a content hash, backed by SQLite.

    Opened per lookup rather than held, so the song folder is never locked on
    Windows by an app that merely looked at it.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hash_cache (
                path     TEXT PRIMARY KEY,
                size     INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                inode    INTEGER NOT NULL,
                sha256   TEXT NOT NULL
            )
            """
            )
            conn.commit()

    def _connect(self):
        return closing(sqlite3.connect(self._db_path, timeout=10))

    def hash_file(self, path: Path) -> str:
        path = Path(path)
        key = str(path)
        stat = path.stat()
        identity = (stat.st_size, stat.st_mtime_ns, stat.st_ino)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT size, mtime_ns, inode, sha256 FROM hash_cache WHERE path = ?",
                (key,),
            ).fetchone()
        if row is not None and tuple(row[:3]) == identity:
            return row[3]

        digest = hash_file(path)
        self._store(key, identity, digest)
        return digest

    def remember(self, path: Path, sha256: str) -> None:
        """Record a hash already known (a file just copied and verified), so it is never read again."""
        path = Path(path)
        stat = path.stat()
        self._store(str(path), (stat.st_size, stat.st_mtime_ns, stat.st_ino), sha256)

    def last_known(self, path: Path) -> str | None:
        """The hash recorded for `path` if its size has not changed since. None otherwise."""
        path = Path(path)
        with self._connect() as conn:
            row = conn.execute("SELECT size, sha256 FROM hash_cache WHERE path = ?", (str(path),)).fetchone()
        try:
            size = path.stat().st_size
        except OSError:
            return None
        return row[1] if row is not None and row[0] == size else None

    def _store(self, key: str, identity: tuple, digest: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hash_cache (path, size, mtime_ns, inode, sha256)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, *identity, digest),
            )
            conn.commit()

    def close(self) -> None:
        """Nothing is held open."""
