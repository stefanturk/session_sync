# Repository format v1

A repository is a content-addressed object store plus an append-only set of
numbered manifests. It is deliberately dumb: any backend that can store bytes
under a name, list names, and refuse to overwrite an existing name can host it.

```
<repo root>/
  repo.json                     metadata (see below)
  manifests/
    v000001.json                one complete description of the project
    v000002.json
  sessions/
    bob@example.com-a1b2.json   advisory "I am working right now" heartbeats
  objects/
    ab/abcdef…                  file contents, named by SHA-256, sharded 2 chars
```

## repo.json

Written once at creation and not depended on afterwards:

```json
{
  "repository_format_version": 1,
  "repository_id": "uuid",
  "project_name": "My Song",
  "project_format": "ableton",
  "project_adapter_version": 1,
  "created_by": {"name": "…", "email": "…"},
  "created_at": "2026-08-16T21:14:00+00:00"
}
```

`project_format` is the only DAW-specific field in the whole storage layer. A
Logic repository differs by that string and nothing else.

## Manifests

One per version, immutable once published:

```json
{
  "manifest_format_version": 1,
  "version": 24,
  "repository_id": "uuid",
  "parent_version": 23,
  "project_format": "ableton",
  "created_at": "…",
  "author": {"name": "Bob", "email": "bob@example.com"},
  "note": "Guitar overdubs",
  "files": {
    "My Song.als": {"sha256": "…", "size": 97407},
    "Samples/Recorded/Sax 0051.aif": {"sha256": "…", "size": 20066304}
  }
}
```

A manifest lists the *complete* project, not a delta — so any version can be
reconstructed on its own, and identical audio across versions is stored once.

Paths are project-relative, use `/`, and are normalised to Unicode NFC. macOS
reports filenames decomposed (NFD); without normalising, the same file looks
renamed the moment it round-trips through another machine.

## The current version

**HEAD is derived, never stored:** the current version is the highest-numbered
manifest that exists. Nothing needs updating to publish a version, which is what
makes a push atomic on storage that has no transactions (Google Drive has no
conditional update).

## Push order — the invariant

1. Check the derived remote HEAD still equals this Mac's base version; if not,
   refuse and require a pull.
2. Upload every object the new manifest will reference that is not already there.
3. **Publish `manifests/v{n+1}.json`. This single create is the commit point**,
   and the backend must refuse it if that version already exists
   (`VersionAlreadyExistsError` → the user is told to pull).

> The current version of a repository never names a file whose content is
> missing.

A push killed at any point before step 3 leaves the repository on its previous
version with some unreferenced objects — wasted space, never a broken project.
`tests/test_crash_safety.py` kills a push at every stage and asserts this.

Unreferenced objects are never deleted automatically. Garbage collection is a
later, explicit, grace-period-protected operation.

## Sessions (advisory)

`sessions/<user>-<id>.json`, refreshed about once a minute while someone has a
session open, deleted on push or when the session ends:

```json
{"key": "bob@example.com-a1b2", "user": "bob@example.com", "display_name": "Bob",
 "host": "Bobs-MBP", "base_version": 25, "state": "working",
 "started_at": "…", "last_heartbeat": "…"}
```

Stale after 15 minutes, abandoned after 24 hours. This is presence information
only — it can tell you Alice is tracking guitars right now, but it can never
lock a repository. A client that crashes simply stops beating. The version
check in step 1 above remains the only real gate.
