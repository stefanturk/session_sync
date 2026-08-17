# SessionSync

Pull, work, push — for Ableton Live projects.

One current version of the project, shared between collaborators. No branches,
no merging of `.als` files. You pull the latest, work in Ableton, review what
changed, and push. If someone else pushed while you were working, you are told
before you start playing — not after two hours of takes.

## Where it stands

| | |
|---|---|
| Engine, manifests, hashing | done |
| Folder-backed storage (a folder, external drive, NAS) | done |
| CLI (`sessionsync`) | done |
| PySide6 Mac app | done (not yet packaged as a `.app`) |
| Test suite, incl. crash and stale-push tests | 225 passing |
| Google Drive backend | next |

Proven on a real 415 MB Live 11 project: byte-for-byte reconstruction of 103
files, `Backup/` and `.asd` correctly ignored, and dedup taking a 34 MB "added"
set down to 95 KB actually uploaded.

## Try it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e .

.venv/bin/sessionsync identity "Your Name" you@example.com
.venv/bin/sessionsync init ~/Music/Ableton/My\ Song --remote ~/SessionSyncRemote/my-song
.venv/bin/sessionsync push -m "Initial version"
```

On the other Mac:

```bash
sessionsync clone ~/SessionSyncRemote/my-song ~/Music/Ableton/My\ Song
sessionsync session start      # refuses if you are behind
sessionsync status             # what changed, and who else is working right now
sessionsync push -m "Guitar overdubs"
```

`sessionsync --help` lists the rest: `review`, `pull`, `history`, `who`,
`session end`.

Or open the window on the same project:

```bash
.venv/bin/python -m sessionsync.ui
```

## Docs

- [`docs/architecture.md`](docs/architecture.md) — the six layers and what each owns
- [`docs/repository_format.md`](docs/repository_format.md) — the on-disk format and the push invariant
- [`docs/ableton_notes.md`](docs/ableton_notes.md) — what a Live project actually contains
- [`docs/gui_plan.md`](docs/gui_plan.md) — the Mac app

## Tests

```bash
.venv/bin/python -m pytest
```

The interesting ones are `test_crash_safety.py` (kills a push at every stage and
asserts the current version never names a missing file) and `test_pull_safety.py`
(a pull never silently replaces work that only exists on your Mac).
