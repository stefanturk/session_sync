# Repository format (v2)

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
  "repository_format_version": 2,
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
  "summary": "Added Sax · Changed Drums",
  "tracks": {"Drums": "9f2c01ab33de", "Sax": "5be1c0a47710"},
  "revision": {"path": "Past Revisions/Rev 0024 - 2026-11-06 - Bob - Guitar overdubs.als",
               "sha256": "…", "size": 97412, "set": "My Song.als",
               "project_marker": "Ableton Project Info"},
  "files": {
    "My Song.als": {"sha256": "…", "size": 97412, "source_sha256": "…"},
    "Samples/Recorded/Sax 0051.aif": {"sha256": "…", "size": 20066304},
    "Samples/Imported/Room Mic.wav": {"sha256": "…", "size": 51200044}
  }
}
```

`summary`, `tracks` and `revision` are optional (older manifests lack them, and
readers ignore fields they do not know):

- `summary` / `tracks`: a one-line description and a per-track content
  fingerprint written at push time.
- `revision`: this push's main Set, saved under `Past Revisions/`. It is not in
  `files`, so it never shows up as a change; it shares its object with the main
  Set's entry.
- `source_sha256` on a file: Consolidate rewrote this Live Set for sharing (its
  samples re-pointed into the project). The value is the hash of the untouched
  Set on the computer that pushed it, which counts as the same content.

For Ableton, `files` is what Consolidate selected, not the whole folder: the Sets,
the audio and Max devices they use (outside files under `Samples/Imported/`), and
small non-audio files. See `ableton_notes.md`.

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

## The shared-folder layout (`MasterFolderStorage`)

The Google Drive shared copy uses the same manifests, but keeps the project's
files visible instead of in `objects/`. Since `repository_format_version` 2
they sit one folder down, and every Live Set carries the suffix `.als-shared`,
so double-clicking in Drive never opens (and saves over) the shared copy:

```
My Song/
  ▶ Open in Live.webloc         sessionsync://open?repo=<repository_id>&song=<name>
  Read me.txt
  Song data — managed by Session Sync/
    My Song.als-shared          the current revision, as Consolidate prepared it
    Samples/…                   every file any revision has used
    Past Revisions/Rev 0001 … .als-shared
  .sessionsync/manifests/       v000001.json + v000001.committed …
  .sessionsync/history/objects/ old content of files that were overwritten
  .sessionsync/staging/         objects of a push in progress
  .sessionsync/rescued/         Live Sets someone saved straight into Drive
  .sessionsync/trash/           anything else tidied away (Live's Backup/ …)
```

Manifests always use the real names (`My Song.als`); only `_visible` maps
them. Format 1 (files at the top, plain `.als`) is still read; `migrate()`
moves it to format 2 under the commit lock, by renames only, writing the format
number last (a `.sessionsync/migrating` marker lets the next client finish a
move that was cut short). An app refuses to pull or push a format number
higher than it knows.

A Live Set in the data folder whose bytes match no manifest — or a plain `.als`
— was saved in Drive directly. `drive_edits()` lists them; `rescue_drive_edits()`
moves them to `rescued/`, restores the pushed set (from history, an older path,
or the rescuer's own matching copy) and moves Live's `Backup/` to `trash/`.

- A file a newer revision no longer uses is **left in place**, never moved or
  deleted, so every Past Revision opens straight from the folder.
- Overwritten content goes to `history/objects` and is kept forever (there is no
  pruning any more; `retention.json` only records what an older Session Sync
  pruned).
- Staged files are **moved** into place, not copied: in a Google Drive folder a
  move is a rename, while a copy would upload the audio a second time.

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
