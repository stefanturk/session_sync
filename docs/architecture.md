# Architecture

Six layers, each replaceable without touching the others.

```
CLI (sessionsync/cli.py)          →  later: PySide6 window, same engine
  Repository engine (core/)       →  pull, work, push; version rules; local safety
    DAW adapter (adapters/)       →  what counts as "the project"
    Storage provider (storage/)   →  where the bytes live
    Identity (auth/)              →  who pushed
```

## core/ — the engine

| Module | Responsibility |
|---|---|
| `repository.py` | `create`, `clone`, `open`, `status`, `review`, `push`, `pull`, `history`, sessions |
| `manifest.py` | Build, serialise and diff manifests; path normalisation |
| `hashing.py` | Streaming SHA-256 plus the cache that makes a 6 GB project scan fast |
| `localstate.py` | `.sessionsync/` — base version, session baseline, hash cache, trash |
| `session.py` | Heartbeats, expiry, and the wording used to describe them |
| `errors.py` | Every deliberate failure, worded for a musician |

The engine knows nothing about Ableton or Google. It asks the adapter which
files matter and the storage provider to keep bytes.

## adapters/ — what counts as "the project"

`DAWAdapter` (`adapters/base.py`): `detect_project`, `inspect_project`,
`validate_project`, `prepare_project`, `enumerate_tracked_files`,
`environment_requirements`.

`AbletonAdapter` additionally offers `referenced_media`, which reads the
gzipped XML of a `.als` and reports which samples live inside the project,
which live elsewhere on this Mac (and so would not travel), and which cannot be
found at all. Live's own Core Library devices and installed Pack content are
filtered out — they exist on every machine with the same Live installed.

A `LogicAdapter` slots in here later; nothing in `core/` or `storage/` changes.

## storage/ — where the bytes live

`StorageProvider` (`storage/base.py`) is deliberately minimal: objects by hash,
numbered manifests, session files, repo metadata. The one rule with teeth is
that `publish_manifest` must refuse a version that already exists.

- `LocalFolderStorage` — a folder, an external drive, a NAS. Real backend, and
  the one the consistency rules are proven against offline.
- `GoogleDriveStorage` — next. Must pass `tests/storage_contract.py` unchanged
  before it is trusted. Version races there are resolved by create-then-list
  (earliest `createdTime` wins, loser deletes its manifest and raises
  `VersionAlreadyExistsError`), because Drive has no conditional update.

## Local working copy

`.sessionsync/` inside the project folder, so the folder stays an ordinary
Ableton project that opens fine without SessionSync installed:

- `state.db` — repository id, remote location, base version, session baseline,
  last push summary, and the hash cache keyed on `(path, size, mtime, inode)`.
- `trash/<timestamp>/…` — anything a pull replaces or removes lands here first.
  Nothing is ever deleted in place.

## Safety rules the tests enforce

1. A push whose base version is stale is refused (`tests/test_repository.py`).
2. HEAD never references a missing object, even if the push is killed at any
   stage (`tests/test_crash_safety.py`).
3. A pull that would replace locally edited, unpushed files stops and names
   them; work that only exists on this Mac is never in the way
   (`tests/test_pull_safety.py`).
4. A crashed client's session heartbeat never blocks anyone
   (`tests/test_session.py`).
