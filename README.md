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
| Consolidate on Push (only used files, outside files copied in, Past Revisions) | done |
| Test suite, incl. crash and stale-push tests | 370 passing |
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
sessionsync push --dry-run                           # what Push would send and leave behind
sessionsync open                                     # open YOUR copy in Live
sessionsync rescue                                   # someone saved the song inside Drive: bring it home
sessionsync open-rev 7                               # open revision 7 in Live
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

### Windows

Every push to GitHub builds the Windows installer (Actions → "Test and build" →
the `SessionSync-windows` artifact): `SessionSync-Setup-0.2.0.exe`, plus a zip
of the same program. The installer needs no administrator rights. It puts the
app in `%LOCALAPPDATA%\Programs\SessionSync` with a Start menu entry, and it
registers `sessionsync:` links, so double-clicking `▶ Open in Live (Windows)`
in a shared song folder opens your copy. The app is unsigned, so the first time
it runs Windows SmartScreen says "Windows protected your PC". Click **More info →
Run anyway**.

To build it on a Windows PC (Python 3.12 and, for the installer, Inno Setup 6):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\smoke_windows.ps1   # install + link check
```

What differs on Windows:
- Your copy goes on the Desktop. If the Desktop is in OneDrive, it goes to
  `Music\Session Sync` instead.
- Live is spotted through the task list and
  `%APPDATA%\Ableton\Live *\Preferences\Log.txt`.
- The log is `%LOCALAPPDATA%\SessionSync\SessionSync.log`.

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
`open-rev`, `session end`.

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


## Shared copy and your own copy (Pull / Push)

Each song folder in Drive looks like this, so nobody can open the shared copy in Live by mistake:

```
Sam Stefan Song Sketches/
  ▶ Open in Live.webloc                       double-click: opens YOUR copy (getting it first)
  ▶ Open in Live (Windows).url                the same, for a Windows PC
  Read me.txt
  Song data — managed by Session Sync/        the project; its Live Sets end in .als-shared
  .sessionsync/                               versions, history, who is working
```

Your own copy goes on your **Desktop** (`~/Desktop/<Song>/`) unless you choose another folder in
Settings. It must be on this computer only: synced folders (Drive, Dropbox, OneDrive, iCloud Drive)
are refused, and a Desktop that iCloud syncs is avoided. You work there; **Push** sends your saved
work, **Pull** brings in everyone else's. There is no "start working" button: when Live has your
copy open you show as working, and while you have unpushed changes the others see that too —
closing the app does not hide them.

If someone does save the song inside Drive (an older app, or a renamed file), the app says so and
**Rescue Edits** brings that save into your copy — beside your set if you have changes of your own —
and puts the shared copy back the way it was pushed. Nothing is deleted.

**Upgrading:** the first Push with this version moves an older shared copy into the new layout (renames
only — Drive uploads nothing again). Update Session Sync on every collaborator's Mac before that first
Push: the old app does not know the new layout.

Every Push first runs **Consolidate**, Session Sync's own Collect All and Save, so nobody has to:

- Only the files the Live Sets actually use are sent. Unused takes and old bounces stay on your Mac.
- Samples used from elsewhere on your Mac (Downloads, a sample library) are copied into the shared
  copy's `Samples/Imported/`. Only the shared copy of the Set is re-pointed at them, and it is checked
  before anything uploads. Your own `.als` is never touched.
- Ableton's own Packs (Core Library) are not copied; Push says which ones a collaborator needs.
- Missing samples are listed (Push still goes). An unreadable Set, or a main Set saved by Live 10,
  stops Push with a reason: re-save it in Live 11 or newer.
- The main Set is saved as `Past Revisions/Rev 0007 - <date> - <who> - <note>.als`.

Nothing a revision used is ever deleted from the shared copy. A sample that Rev 8 stops using stays where
Rev 7 had it, so every Past Revision can be rebuilt. Pull brings down what the
current revision uses, plus the small Past Revisions Sets. **Open in Live** (History) or `open-rev N`
fetches whatever an old revision needs first.

- Push only works if you started from the newest version; otherwise **Catch Up** saves your whole
  version aside first, brings theirs, and lists who changed which tracks.
- The shared copy changes only when a push succeeds. A crash part way is finished or undone by the next client.
- Others see "working now" (from the app, and Live's own log) and "has changes not pushed" until they do.
- Close the song in Live (or quit Live) while Session Sync is open and, within a few seconds, it asks
  whether to push. If someone pushed in the meantime, it offers Catch Up instead. **Not Now** leaves
  everything on your computer, and the others see that you have changes waiting.
- Saying Not Now and then finding someone else pushed loses nothing. Pull refuses to replace your
  changes, and Catch Up first copies your whole version beside the project as
  `<Song> (mine before vN, …)`. It then brings theirs in and keeps your set next to theirs as
  `<Song> (mine).als`.
- If Google Drive is still syncing, pulling stops with a message instead of half-copying.
- Not built yet: automatically combining two edited sets (Catch Up keeps both sets side by side instead).
