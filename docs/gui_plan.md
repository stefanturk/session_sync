# Step 6 — the Mac app

## Running it

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
| `presenter.py` | **What the window says** — banner, button states, notification-worthy news. No Qt, fully unit tested. |
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

## The window

```
Take My Heart Example Project

  Latest version: v3      You have: v3
  ┌──────────────────────────────────────────────────────────┐
  │ You have work that has not been pushed yet.              │
  └──────────────────────────────────────────────────────────┘
  ●  Alice has been working since 8:02 PM.
  ⚠  1 audio file lives outside this project folder…

  [ Pull Latest ] [ Start Session ] [ Review Changes ] [ PUSH SESSION ]

  Change     File                            Size
  Modified   Take My Heart.als             95.1 KB

  Project: 458.7 MB    Already stored: 458.7 MB    To upload: 0 B
  Last push: v3 — 16 Aug 2026, 10:08 PM — Guitar overdub
```

Rules the tests hold to:

- **The banner is the loudest element**, coloured by severity, and being behind
  outranks having unpushed work.
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

## Still to do

- Packaging as a `.app` with PyInstaller.
- Setting up a *new* project from the window (currently CLI only).
- A project list instead of one window per project — the single-project window
  is the whole workflow, so this is comfort, not capability.
- Progress inside a long push/pull (currently a status message, no percentage) —
  wants byte-level callbacks from `Repository.push`.
