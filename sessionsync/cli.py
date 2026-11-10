"""The SessionSync command line.

Deliberately worded for musicians: pull latest, start a session, review
changes, push. The GUI will drive exactly this engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.auth.identity import load_identity, save_identity
from sessionsync.core import osinfo
from sessionsync.core.errors import CloudRootError, SessionSyncError
from sessionsync.core.repository import Repository
from sessionsync.core.session import describe_sessions
from sessionsync.storage import storage_from_location

app = typer.Typer(add_completion=False, help="Pull, work, push — for Ableton projects.")
session_app = typer.Typer(help="Working sessions.")
app.add_typer(session_app, name="session")
hub_app = typer.Typer(help="Shared folders you collaborate through.")
app.add_typer(hub_app, name="hub")
song_app = typer.Typer(help="Songs inside a shared folder.")
app.add_typer(song_app, name="song")

PROJECT_OPTION = typer.Option(Path.cwd, "--project", "-p", help="The project folder.")


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def local_time(timestamp: str | None) -> str:
    """Versions are stamped in UTC; people read them in their own timezone."""
    if not timestamp:
        return ""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return osinfo.strftime(parsed.astimezone(), "%d %b %Y, %-I:%M %p")


def warn_about_media(repo: Repository) -> None:
    """What Push will do with this project's audio (Consolidate), said up front."""
    try:
        plan = repo.plan_push(rewrite=False)
    except Exception:  # noqa: BLE001 - a status line must never be the failure
        return
    if plan is not None:
        describe_plan(plan)


def _first_few(paths, limit: int = 5) -> None:
    for path in list(paths)[:limit]:
        typer.echo(f"    {path}")
    if len(paths) > limit:
        typer.echo(f"    ...and {len(paths) - limit} more")


def describe_plan(plan) -> None:
    """Consolidate's findings: what is copied in, left behind, missing, or blocking."""
    for problem in plan.problems:
        typer.echo(f"\n  Cannot push: {problem}")
    if plan.imported:
        typer.echo(
            f"\n  From elsewhere on this computer ({len(plan.imported)}, "
            f"{human_size(plan.imported_bytes)}) — copied into Samples/Imported on Push:"
        )
        _first_few([str(p) for p in plan.imported.values()])
    if plan.unused:
        typer.echo(
            f"\n  Not used by any Live Set ({len(plan.unused)}, {human_size(plan.unused_bytes)}) "
            "— stays on this computer, not pushed:"
        )
        _first_few(plan.unused)
    if plan.missing:
        typer.echo(f"\n  Used by the song but nowhere on this computer ({len(plan.missing)}):")
        _first_few(plan.missing)
    if plan.skipped_sets:
        typer.echo("\n  Sets from Live 9 or older left out (re-save them in Live 10 or newer to share them):")
        _first_few(plan.skipped_sets)
    if plan.packs:
        typer.echo(f"\n  Collaborators need these Ableton Packs: {', '.join(plan.packs)}")


def _open(project: Path) -> Repository:
    try:
        return Repository.open(project, author=load_identity())
    except SessionSyncError as exc:
        _fail(exc)
    except FileNotFoundError as exc:
        _fail(exc)


def _fail(message) -> None:
    typer.echo(str(message))
    raise typer.Exit(code=1)


def _print_files(title: str, paths: list[str], sizes: dict[str, int] | None = None) -> None:
    if not paths:
        return
    typer.echo(f"\n{title}")
    for path in paths:
        if sizes and path in sizes:
            typer.echo(f"  {path}   {human_size(sizes[path])}")
        else:
            typer.echo(f"  {path}")


# --- setup ---------------------------------------------------------------


@app.command()
def identity(name: str, email: str) -> None:
    """Tell SessionSync who you are (shown next to your versions)."""
    saved = save_identity(name, email)
    typer.echo(f"You are {saved['name']} <{saved['email']}>.")


@app.command()
def init(
    project: Path = typer.Argument(..., help="An existing Ableton project folder."),
    remote: Path = typer.Option(..., "--remote", help="Where the repository lives."),
) -> None:
    """Start syncing an Ableton project you already have."""
    adapter = AbletonAdapter()
    try:
        repo = Repository.create(
            project,
            storage_from_location(str(remote)),
            adapter=adapter,
            author=load_identity(),
        )
    except SessionSyncError as exc:
        _fail(exc)

    typer.echo(f"'{repo.project_root.name}' is now synced to {remote}.")
    validation = adapter.validate_project(project)
    for warning in validation.warnings:
        typer.echo(f"\nHeads up: {warning}")
    typer.echo("\nPush your first version with:  sessionsync push --note \"Initial version\"")


@app.command()
def clone(
    remote: Path = typer.Argument(..., help="The repository location."),
    project: Path = typer.Argument(..., help="Where to put the project on this computer."),
) -> None:
    """Download someone else's project onto this computer."""
    try:
        repo = Repository.clone(
            storage_from_location(str(remote)), project, author=load_identity()
        )
    except SessionSyncError as exc:
        _fail(exc)

    typer.echo(f"Downloaded '{repo.project_root.name}' (v{repo.base_version}) to {project}.")


# --- day to day ----------------------------------------------------------


@app.command()
def status(project: Path = PROJECT_OPTION) -> None:
    """Where this project stands, and who else is working on it."""
    repo = _open(project)
    st = repo.status()

    typer.echo(f"{repo.project_root.name}")
    typer.echo(f"\n  Latest version: v{st.remote_version}    You have: v{st.local_version}")

    if st.state == "up_to_date":
        typer.echo("  Up to date.")
    elif st.state == "behind":
        typer.echo(
            f"  You are {st.versions_behind} version(s) behind — Pull latest before you start."
        )
    elif st.state == "local_changes":
        typer.echo("  You have work that has not been pushed yet.")
    elif st.state == "push_blocked":
        typer.echo(
            f"  You are {st.versions_behind} version(s) behind and you have unpushed work.\n"
            "  Pull latest first — your own recordings are kept."
        )

    if st.drive_edits:
        typer.echo(
            "\n  Someone saved the song inside the shared folder in Google Drive:\n"
            + "\n".join(f"    {p}" for p in st.drive_edits)
            + "\n  Run `sessionsync rescue` to bring that save into your copy."
        )

    others = describe_sessions(st.active_sessions)
    if others:
        typer.echo("\n" + "\n".join(f"  {line}" for line in others.splitlines()))

    warn_about_media(repo)

    diff = st.diff
    if diff.has_changes:
        sizes = diff.sizes
        _print_files("Added", diff.added, sizes)
        _print_files("Modified", diff.modified, sizes)
        _print_files("Deleted", diff.deleted)
        typer.echo(
            f"\n  Project: {human_size(st.project_bytes)}"
            f"    Already stored: {human_size(st.already_stored_bytes)}"
            f"    To upload: {human_size(st.upload_bytes)}"
        )


@app.command()
def review(project: Path = PROJECT_OPTION) -> None:
    """What has changed since you started."""
    repo = _open(project)
    diff = repo.review()
    if not diff.has_changes:
        typer.echo("Nothing has changed yet.")
        return

    _print_files("Added", diff.added, diff.sizes)
    _print_files("Modified", diff.modified, diff.sizes)
    _print_files("Deleted", diff.deleted)
    typer.echo(f"\nUpload: {human_size(diff.changed_bytes)}")


@app.command()
def push(
    project: Path = PROJECT_OPTION,
    note: str = typer.Option("", "--note", "-m", help="What you did this session."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what Push would send and leave behind; change nothing."
    ),
) -> None:
    """Consolidate, then publish this session's work as the new current version.

    Only the files the Live Sets use are sent. Sounds from elsewhere on this computer
    are copied into the shared copy's Samples/Imported, and the main Set is saved
    into Past Revisions.
    """
    from sessionsync.core.errors import NothingToPushError

    repo = _open(project)
    if dry_run:
        status = repo.status()
        typer.echo(
            f"Push would send {len(status.diff.added)} new and {len(status.diff.modified)} "
            f"changed files ({human_size(status.upload_bytes)} to upload; the song is "
            f"{human_size(status.project_bytes)} in all)."
        )
        if status.plan is not None:
            describe_plan(status.plan)
        return
    repo.heartbeat(state="pushing")
    try:
        result = repo.push(note=note or None)
    except NothingToPushError as exc:
        typer.echo(str(exc))
        return
    except SessionSyncError as exc:
        repo.heartbeat(state="working")
        _fail(exc)

    typer.echo(f"Pushed v{result.version}.")
    typer.echo(
        f"  Added: {len(result.diff.added)}   Modified: {len(result.diff.modified)}"
        f"   No longer used: {len(result.diff.deleted)}   Uploaded: {human_size(result.uploaded_bytes)}"
    )
    plan = repo.last_plan
    if plan is not None:
        if plan.imported:
            typer.echo(f"  Copied in from elsewhere on this computer: {len(plan.imported)}")
        if plan.unused:
            typer.echo(
                f"  Left on this computer (unused): {len(plan.unused)} ({human_size(plan.unused_bytes)})"
            )
        if plan.missing:
            typer.echo(f"  Missing here, so not shared: {len(plan.missing)}")


@app.command()
def pull(project: Path = PROJECT_OPTION) -> None:
    """Get the latest version of the project (plus the small Past Revisions Sets)."""
    from sessionsync.core.errors import LocalChangesWouldBeReplacedError

    repo = _open(project)
    try:
        result = repo.pull()
    except LocalChangesWouldBeReplacedError as exc:
        typer.echo(str(exc))
        typer.echo(
            f"\nYour versions would be kept in {repo.state.trash_dir}, "
            "but they would leave the project."
        )
        if not typer.confirm("\nReplace your versions with the latest ones?", default=False):
            typer.echo("Nothing was changed. Push your work first if you want to keep it.")
            return
        result = repo.pull(replace_my_versions=True)
        typer.echo(f"Your previous versions are in {repo.state.trash_dir}.")

    if result.to_version == result.from_version:
        typer.echo(f"Already on the latest version (v{result.to_version}).")
        return

    typer.echo(f"Updated to v{result.to_version}.")
    _print_files("Added", result.diff.added)
    _print_files("Updated", result.diff.modified)
    _print_files("No longer used (left on this computer)", result.diff.deleted)


@app.command()
def history(project: Path = PROJECT_OPTION) -> None:
    """Previous sessions, newest first."""
    repo = _open(project)
    for entry in repo.history():
        who = (entry.author or {}).get("name", "Someone")
        when = local_time(entry.created_at)
        note = entry.note or "(no note)"
        typer.echo(f"v{entry.version}  {when}  {who} — {note}")
        typer.echo(f"      {entry.summary}")


@app.command()
def restore(
    version: int = typer.Argument(..., help="The version to bring back."),
    to: Path = typer.Option(..., "--to", help="A NEW folder to build it in."),
    project: Path = PROJECT_OPTION,
) -> None:
    """Rebuild an earlier version in a separate folder. Your project is untouched."""
    repo = _open(project)
    try:
        folder = repo.restore_copy(version, to)
    except (FileExistsError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"v{version} is in {folder}. Your current project was not changed.")


@app.command("open-rev")
def open_rev(
    version: int = typer.Argument(..., help="The revision to open."),
    project: Path = PROJECT_OPTION,
    no_open: bool = typer.Option(False, "--no-open", help="Get it ready but do not open Live."),
) -> None:
    """Open an old revision in Live, fetching any audio it needs first."""

    repo = _open(project)
    try:
        live_set = repo.open_revision(version)
    except (SessionSyncError, ValueError, FileNotFoundError) as exc:
        _fail(exc)
    if live_set.parent.name == "Past Revisions":
        typer.echo(f"Rev {version} is ready: {live_set}")
    else:
        typer.echo(
            f"Some of Rev {version}'s audio is different here now, so it was built as a "
            f"separate copy: {live_set}"
        )
    if not no_open:
        osinfo.open_with_default_app(live_set)


@app.command("open")
def open_live(
    project: Path = PROJECT_OPTION,
    no_open: bool = typer.Option(False, "--no-open", help="Only say which set it would open."),
) -> None:
    """Open your copy of the song in Live (never the shared copy in Drive)."""

    repo = _open(project)
    try:
        target = repo.open_target()
    except SessionSyncError as exc:
        _fail(exc)
    behind = repo.remote_version() - repo.base_version
    if behind > 0:
        typer.echo(f"Note: you are {behind} version(s) behind — `sessionsync pull` first.")
    typer.echo(f"Opening {target}")
    repo.refresh_presence(live_here=True)
    if not no_open:
        osinfo.open_with_default_app(target)


@app.command()
def rescue(project: Path = PROJECT_OPTION) -> None:
    """Bring a save made straight into the shared Drive copy into your copy."""
    repo = _open(project)
    edits = repo.drive_edits()
    if not edits:
        typer.echo("Nothing to rescue: nobody saved the song inside the shared folder.")
        return
    try:
        rescued = repo.rescue_drive_edits()
    except SessionSyncError as exc:
        _fail(exc)
    for item in rescued:
        if item.replaced:
            typer.echo(f"{item.path.name} now has the edit. Push when you are happy with it.")
        else:
            typer.echo(f"Saved beside your set as {item.path} — open it in Live to compare.")
    typer.echo("The shared copy is back the way it was pushed.")


@app.command()
def who(project: Path = PROJECT_OPTION) -> None:
    """Who else is working on this project right now."""
    repo = _open(project)
    text = describe_sessions(repo.other_sessions())
    typer.echo(text or "Nobody else is working on this project right now.")


@app.command()
def catchup(project: Path = PROJECT_OPTION) -> None:
    """Someone pushed first: save your version aside, bring theirs, say who did what."""
    from sessionsync.core.summary import describe_catch_up

    repo = _open(project)
    try:
        result = repo.catch_up()
    except (SessionSyncError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"Caught up to v{result.pulled.to_version}.\n")
    typer.echo(describe_catch_up(result))


@app.command()
def pin(
    version: int = typer.Argument(..., help="The version to keep for good."),
    project: Path = PROJECT_OPTION,
    unpin: bool = typer.Option(False, "--unpin", help="Stop keeping it."),
) -> None:
    """Mark a version as one to keep (every version is kept; this labels it)."""
    repo = _open(project)
    pin_version = getattr(repo.storage, "pin_version", None)
    if pin_version is None:
        _fail("Only shared-folder projects keep a limited history.")
    try:
        pin_version(version, not unpin)
    except ValueError as exc:
        _fail(exc)
    typer.echo(f"v{version} will {'no longer ' if unpin else ''}be kept for good.")


# --- sessions ------------------------------------------------------------


@session_app.command("start")
def session_start(
    project: Path = PROJECT_OPTION,
    force: bool = typer.Option(False, "--force", help="Start anyway (e.g. offline)."),
) -> None:
    """Begin working: checks you are current and tells the others."""
    repo = _open(project)
    try:
        repo.start_session(force=force)
    except SessionSyncError as exc:
        _fail(exc)

    typer.echo(f"Session started on v{repo.base_version}. Go and make some noise.")
    others = describe_sessions(repo.other_sessions())
    if others:
        typer.echo("\n" + others)


@session_app.command("end")
def session_end(project: Path = PROJECT_OPTION) -> None:
    """Finish working without pushing."""
    repo = _open(project)
    repo.end_session()
    typer.echo("Session ended. Nothing was uploaded.")


# --- shared folders -------------------------------------------------------


def _settings():
    from sessionsync.ui.settings import Settings

    return Settings()


def _find_hub(name: str):
    for hub in _settings().hubs():
        if hub.name.lower() == name.lower():
            return hub
    _fail(f"No shared folder called '{name}'. See: sessionsync hub list")


@hub_app.command("add")
def hub_add(
    folder: Path = typer.Argument(..., help="The shared folder (e.g. in Google Drive)."),
    name: str = typer.Option(None, "--name", help="What to call it (default: the folder name)."),
) -> None:
    """Connect a collaborator's shared folder once; its songs appear automatically."""
    if not folder.expanduser().is_dir():
        _fail(f"{folder} is not a folder.")
    try:
        hub = _settings().add_hub(folder, name)
    except CloudRootError as exc:
        _fail(str(exc))
    typer.echo(f"Connected '{hub.name}' ({len(hub.songs())} song(s) found).")


@hub_app.command("list")
def hub_list() -> None:
    """Shared folders you have connected, with the songs in each."""
    hubs = _settings().hubs()
    if not hubs:
        typer.echo("No shared folders yet. Connect one with: sessionsync hub add <folder>")
    for hub in hubs:
        typer.echo(f"{hub.name}  ({hub.path})")
        for song in hub.songs():
            who = f" — {song.latest_author}" if song.latest_author else ""
            typer.echo(f"   {song.name}  v{song.latest_version}{who}")


@hub_app.command("news")
def hub_news() -> None:
    """What changed in your shared folders since you last looked."""
    from sessionsync.core.hub import compare_scans

    settings = _settings()
    nothing = True
    for hub in settings.hubs():
        events, seen = compare_scans(hub, settings.seen(hub))
        settings.set_seen(hub, seen)
        for event in events:
            nothing = False
            if event.kind == "new_song":
                typer.echo(f"{hub.name}: new song '{event.song}'")
            else:
                for author, text in zip(event.authors, event.summaries):
                    typer.echo(f"{hub.name}: {author} on '{event.song}' — {text}")
    if nothing:
        typer.echo("Nothing new.")


@song_app.command("new")
def song_new(
    hub_name: str = typer.Argument(..., help="Which shared folder."),
    project: Path = typer.Argument(..., help="Your Ableton project folder."),
    name: str = typer.Option(None, "--name", help="Song name (default: the project folder name)."),
) -> None:
    """Share one of your Ableton projects through a shared folder."""
    hub = _find_hub(hub_name)
    try:
        repo = hub.add_song(name or project.name, project, load_identity())
    except (SessionSyncError, FileExistsError) as exc:
        _fail(exc)
    typer.echo(f"'{repo.project_root.name}' is now shared in {hub.name} (v{repo.base_version}).")


@song_app.command("add")
def song_add(
    hub_name: str = typer.Argument(..., help="Which shared folder."),
    project: Path = typer.Argument(..., help="Your Ableton project folder."),
    name: str = typer.Option(None, "--name", help="Song name (default: the project folder name)."),
) -> None:
    """Add a project from this computer to a shared folder (your folder becomes your working copy)."""
    song_new(hub_name, project, name)


@song_app.command("get")
def song_get(
    hub_name: str = typer.Argument(..., help="Which shared folder."),
    song: str = typer.Argument(..., help="The song to get."),
    to: Path = typer.Option(None, "--to", help="Where to put your copy (anywhere)."),
) -> None:
    """Make your own working copy of a shared song, anywhere on this computer."""
    hub = _find_hub(hub_name)
    settings = _settings()
    try:
        repo = hub.join_song(song, settings.local_root(), load_identity(), to)
    except (SessionSyncError, FileExistsError) as exc:
        _fail(exc)
    settings.set_song_path(hub, song, repo.project_root)
    typer.echo(f"Your copy of '{song}' is at {repo.project_root}.")


@song_app.command("join")
def song_join(
    hub_name: str = typer.Argument(..., help="Which shared folder."),
    song: str = typer.Argument(..., help="The song to download."),
) -> None:
    """Download a song from a shared folder onto this computer."""
    hub = _find_hub(hub_name)
    try:
        repo = hub.join_song(song, _settings().local_root(), load_identity())
    except (SessionSyncError, FileExistsError) as exc:
        _fail(exc)
    typer.echo(f"Downloaded '{song}' to {repo.project_root}.")


if __name__ == "__main__":
    app()
