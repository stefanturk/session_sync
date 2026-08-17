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
from sessionsync.core.errors import SessionSyncError
from sessionsync.core.repository import Repository
from sessionsync.core.session import describe_sessions
from sessionsync.storage import storage_from_location

app = typer.Typer(add_completion=False, help="Pull, work, push — for Ableton projects.")
session_app = typer.Typer(help="Working sessions.")
app.add_typer(session_app, name="session")

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
    return parsed.astimezone().strftime("%d %b %Y, %-I:%M %p")


def warn_about_media(repo: Repository) -> None:
    """Tell the user now if the project would arrive incomplete."""
    referenced = getattr(repo.adapter, "referenced_media", None)
    if referenced is None:
        return
    report = referenced(repo.project_root)
    if report.outside:
        typer.echo(
            f"\n  Audio outside the project folder ({len(report.outside)}) — "
            "collaborators would be missing it. Run Collect All and Save in Ableton."
        )
        for path in report.outside[:5]:
            typer.echo(f"    {path}")
        if len(report.outside) > 5:
            typer.echo(f"    ...and {len(report.outside) - 5} more")
    if report.missing:
        typer.echo(f"\n  Audio this Mac cannot find at all ({len(report.missing)}):")
        for path in report.missing[:5]:
            typer.echo(f"    {path}")


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
    project: Path = typer.Argument(..., help="Where to put the project on this Mac."),
) -> None:
    """Download someone else's project onto this Mac."""
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
) -> None:
    """Publish this session's work as the new current version."""
    from sessionsync.core.errors import NothingToPushError

    repo = _open(project)
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
        f"   Removed: {len(result.diff.deleted)}   Uploaded: {human_size(result.uploaded_bytes)}"
    )


@app.command()
def pull(project: Path = PROJECT_OPTION) -> None:
    """Get the latest version of the project."""
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
    _print_files("Removed (kept in .sessionsync/trash)", result.diff.deleted)


@app.command()
def history(project: Path = PROJECT_OPTION) -> None:
    """Previous sessions, newest first."""
    repo = _open(project)
    for entry in repo.history():
        who = (entry.author or {}).get("name", "Someone")
        when = local_time(entry.created_at)
        note = entry.note or "(no note)"
        typer.echo(f"v{entry.version}  {when}  {who} — {note}")
        typer.echo(
            f"      {len(entry.added)} added, {len(entry.modified)} changed, "
            f"{len(entry.deleted)} removed"
        )


@app.command()
def who(project: Path = PROJECT_OPTION) -> None:
    """Who else is working on this project right now."""
    repo = _open(project)
    text = describe_sessions(repo.other_sessions())
    typer.echo(text or "Nobody else is working on this project right now.")


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


if __name__ == "__main__":
    app()
