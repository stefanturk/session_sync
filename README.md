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
| PySide6 Mac app, packaged as a `.app` | done |
| Shared folders ("hubs"), version summaries, restore a copy, clickable banners | done |
| Test suite, incl. crash and stale-push tests | 298 passing |
| Google Drive backend | next |

Proven on a real 415 MB Live 11 project: byte-for-byte reconstruction of 103
files, `Backup/` and `.asd` correctly ignored, and dedup taking a 34 MB "added"
set down to 95 KB actually uploaded.

## Working with collaborators

Connect a shared folder once (for example a Google Drive folder that Drive for
desktop keeps on your Mac) and every Ableton song inside it appears, including
songs added later. Anything else in the folder is ignored.

```bash
sessionsync hub add "~/My Drive/Song Collaboration/Sam Stefan Sketches" --name Sam
sessionsync song new Sam "~/Music/Ableton/My Song"   # share a project you have
sessionsync song join Sam "Song B"                   # download one of theirs
sessionsync hub news                                 # what changed since you looked
sessionsync history                                  # each version, in plain words
sessionsync restore 7 --to "~/Desktop/My Song v7"    # an old version, in a new folder
```

Versions are described automatically, e.g. "Added Sax · Changed Drums · 3 new
samples". Restoring never touches your project: it builds the old version in a
folder of its own so you can open it in Live and take what you want. In the app,
songs are checked every minute; a closed song with nothing unpushed is pulled by
itself, and a banner says what happened. Clicking the banner opens that song.

## The app

Build a double-clickable Mac app that needs no Python installed:

```bash
scripts/build_app.sh          # → ~/Applications/SessionSync.app  (about 100 MB)
open ~/Applications/SessionSync.app
```

To keep it in the Dock: launch it, then right-click its Dock icon →
Options → Keep in Dock.

It opens the last project you worked on, or asks for a folder. On first launch
macOS asks for permission to read the folder your projects are in.

It installs into `~/Applications` rather than leaving the bundle in `dist/`,
because the Dock pins whichever bundle you dragged from — and `dist/` is deleted
on every rebuild. Anything that fails to start is logged to
`~/Library/Logs/SessionSync.log` and reported in a dialog.

For the `sessionsync` command without managing a virtualenv yourself:

```bash
pipx install .                # puts `sessionsync` on your PATH
```

## From source

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
