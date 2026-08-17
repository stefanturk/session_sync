# Step 6 — the Mac app

## Running it

As an app, with no Python involved:

```bash
scripts/build_app.sh      # → dist/SessionSync.app, then drag it to /Applications
```

From source:

```bash
.venv/bin/python -m sessionsync.ui                    # last project, or a folder picker
.venv/bin/python -m sessionsync.ui ~/Music/Ableton/My\ Song
```

Installed, that is `sessionsync-app`. A project must already be set up with
`sessionsync init` — the window opens existing working copies, it does not
create them yet.

## How it is put together

| File | Responsibility |
|---|---|
| `presenter.py` | **What the window says** — banner, header status, button states, notification-worthy news. No Qt, fully unit tested. |
| `theme.py` | The palette and the whole stylesheet, in one place. |
| `widgets.py` | The indicator light — the one thing a stylesheet cannot express. |
| `engine.py` | The `Repository`, on its own thread. Everything is serialised through it. |
| `window.py` | Layout and wiring only. |
| `settings.py` | Recent projects, in `~/.sessionsync/ui.json`. |
| `notify.py` | macOS notifications via `osascript`. |
| `app.py` | Identity on first launch, then which project to open. |

Two reasons the engine has its own thread: scanning a large project takes real
time, and `Repository` owns SQLite connections, which belong to the thread that
created them. So the repository is *created on* the engine thread and never
leaves it — which also means a background refresh can never run inside a push.

The window talks to it only through signals, which Qt queues across the thread
boundary.

## The look

Borrowed from Beat Thief, which sits open next to Ableton all day and is built
as a piece of hardware rather than a web page: dark panel, silkscreen section
labels, an indicator light, and one lit call-to-action. Avenir Next, which
ships with macOS, so nothing is fetched and nothing falls back to Helvetica.

The single addition is amber. Beat Thief only needed good/bad; *behind* is
neither — not an error, but a stop-and-pull-first — and it has to be
distinguishable at a glance from having unpushed work.

Two Qt traps worth remembering:

- `QLabel` is a `QFrame`, so an unqualified `QFrame { border… }` rule puts a
  border around every label inside the frame. Select by object name.
- A stylesheet `color` on `QTreeWidget::item` beats `setForeground`, so the
  per-row colour coding silently does nothing.

## The window

```
  PROJECT                                    ● UNPUSHED WORK
  Midnight Drive                       Latest: v1   You have: v1
 ─────────────────────────────────────────────────────────────
  ┌───────────────────────────────────────────────────────────┐
  │ You have work that has not been pushed yet.               │
  │ ●  Alice has been working since 10:56 PM.                 │
  │ ⚠  1 audio file lives outside this project folder…        │
  └───────────────────────────────────────────────────────────┘

  [ PULL LATEST ] [ START SESSION ] [ REVIEW CHANGES ] [ PUSH SESSION ]

  CHANGES              Project 61.1 MB · Stored 28.5 MB · Upload 32.6 MB
  ┌───────────────────────────────────────────────────────────┐
  │ CHANGE    FILE                                      SIZE  │
  │ ADDED     Samples/Recorded/Sax Overdub.aif       32.6 MB  │
  └───────────────────────────────────────────────────────────┘

  Last push: v1 — 16 Aug 2026, 10:56 PM — Rhodes and a vocal take
```

Rules the tests hold to:

- **The banner is the loudest element**, coloured by severity, and being behind
  outranks having unpushed work. The header light and status word carry the
  same colour, so the state is readable from across a desk.
- **The light pulses only when someone else is pushing.** That is the one thing
  worth catching out of the corner of your eye — a status word that merely
  changes is easy to miss when you glance over from Ableton.
- **Exactly one button is the recommended action** and it is drawn in blue.
  Catching up always outranks publishing, so `pull` wins over `push`.
- **Buttons are disabled, never hidden**, and a disabled button always says why
  in its tooltip.
- **A background check does not grey the window out.** Only a push or a pull
  does. Greying everything for several seconds each minute reads as a hang.
- Media that would not travel is shown *before* a push, not after.

## Behaviour

- Refresh every 60 s, heartbeat every 60 s while a session is open.
- macOS notification when someone else starts working, starts pushing, finishes,
  or when a new version appears — you will be inside Ableton, not looking here.
- The first refresh after launch never notifies: everything is new when you open
  the app, and being told so is noise.
- Pull that would replace unpushed edits shows the same confirmation the CLI
  does, naming the files, defaulting to keeping your work.
- Stale push and "you are behind" are explained, not just refused.

## Packaging

`packaging/SessionSync.spec` + `scripts/build_app.sh` produce a ~100 MB
`~/Applications/SessionSync.app`, ad-hoc signed so the Mac that built it will
open it without the "damaged" dialog.

Things a bundled app does differently from a shell, all handled:

- It inherits a minimal `PATH`, so `osascript` is called by full path and
  "reveal in Finder" goes through `QDesktopServices` rather than `open`.
- Finder passes a `-psn_0_12345` process serial number as an argument, which
  must not be taken for a project folder.
- There is no terminal, so a failure to start would be silent. `packaging/
  entry.py` logs to `~/Library/Logs/SessionSync.log` and raises a dialog.
- Ableton projects live in Desktop/Documents/Downloads, which macOS gates.
  The `NS*FolderUsageDescription` keys give that prompt a reason.

**It installs to `~/Applications`, not `dist/`.** The Dock pins the bundle it
was dragged from, and `dist/` is deleted on every rebuild — a Dock icon pointing
there breaks silently, because the Dock keeps showing the icon either way. The
build also sweeps away stray copies for the same reason.

The icon is drawn by `scripts/render_icon.py` with QPainter — Qt is already a
dependency, so no image library and no binary checked into the repository.

Most of the 100 MB is Qt. The spec excludes what a four-button window has no
use for — WebEngine, Quick/QML, 3D, Multimedia, Charts.

Not signed with a Developer ID and not notarised, so another Mac will warn on
first launch. That needs a paid Apple developer account.

## Still to do

- Setting up a *new* project from the window (currently CLI only).
- A project list instead of one window per project — the single-project window
  is the whole workflow, so this is comfort, not capability.
- Progress inside a long push/pull (currently a status message, no percentage) —
  wants byte-level callbacks from `Repository.push`.
