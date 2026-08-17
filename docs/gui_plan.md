# Step 6 — the Mac app

## State of play

Steps 1–4 are done: engine, folder-based storage, CLI, 163 passing tests
(`.venv/bin/python -m pytest`). Google Drive (Step 5) is deliberately deferred —
the GUI works against a folder remote today. See `architecture.md` and
`repository_format.md`.

Python 3.12 venv at `.venv/`. Test project:
`~/Desktop/SessionSync Test/Take My Heart Example Project` (real Live 11 project,
415 MB, already init'd and pushed to v3 against a scratchpad remote).

## The engine API the window drives

Nothing new needs building in `core/` — the window calls exactly what the CLI calls:

```python
repo = Repository.open(project_root, author=load_identity())
st   = repo.status()      # .state, .local_version, .remote_version, .versions_behind,
                          # .diff, .project_bytes, .upload_bytes, .already_stored_bytes,
                          # .active_sessions, .has_local_changes
repo.start_session(force=False)   # raises ProjectBehindError when behind
repo.heartbeat(state="working"|"pushing")
repo.end_session()
repo.review()                     # ManifestDiff since session start
repo.push(note=...)               # raises StalePushError / NothingToPushError
repo.pull(replace_my_versions=False)  # raises LocalChangesWouldBeReplacedError
repo.history()                    # list[VersionSummary], newest first
repo.adapter.referenced_media(root)   # .inside / .outside / .missing
```

`status.state` is one of: `up_to_date`, `behind`, `local_changes`, `push_blocked`.

## The window

```
My Song

  Latest version: v25        You have: v23
  ⚠  You are 2 versions behind — Pull latest before you start
  ●  Alice has been working since 8:02 PM (from v25)

  [ PULL LATEST ]  [ START SESSION ]  [ REVIEW CHANGES ]  [ PUSH SESSION ]

  Last push: 16 Aug 2026, 9:14 PM — 3 added / 512 MB
```

Rules:
- **Freshness banner is the loudest element**, not a status line. Behind → the
  banner and Pull are what the eye lands on; Start Session is disabled.
- Presence line whenever someone else is active.
- Buttons enable/disable by `status.state`; never hide them.
- Media warnings (audio outside the project) shown before a push, not after.

## Behaviour

- Background refresh every 60 s: `repo.status()` off the UI thread, plus
  `repo.heartbeat()` while a session is open. A `QThread`/worker — never block
  the UI on a scan; a 6 GB project scan takes real time even with the hash cache.
- Notify (macOS notification) when someone else starts or finishes a session,
  and when the remote version changes while the window is open.
- Long operations (push/pull) need a progress state and must not freeze the app.
- Dialogs mirror the CLI's safety prompts: the pull-would-replace-my-work
  confirmation, and the stale-push explanation.

## Decisions still open

- Single-project window vs. a project list. Simplest first: one project, opened
  from a folder picker, remembered in `~/.sessionsync/`.
- Packaging (PyInstaller `.app`) can come after the window works when run with
  `python -m sessionsync.ui`.
