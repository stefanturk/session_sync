"""What the window says, worked out without Qt in the way.

Everything the user reads — the banner, why a button is greyed out, which
changes are notification-worthy — is decided here, so it can be tested without
starting an application. The window below is then only layout and wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sessionsync.core.repository import Status
from sessionsync.core.session import describe_sessions


@dataclass
class Banner:
    text: str
    level: str  # ok | info | warning | danger


@dataclass
class Button:
    label: str
    enabled: bool
    reason: str = ""  # shown as a tooltip when disabled — never leave a user guessing


@dataclass
class Screen:
    title: str
    version_line: str
    banner: Banner
    presence: str
    pull: Button
    start: Button
    review: Button
    push: Button
    changes: list[tuple[str, str, str]] = field(default_factory=list)
    summary: str = ""
    last_push: str = ""
    #: name of the one button to draw attention to — "" when nothing is pressing.
    primary: str = ""
    #: short uppercase status for the header, next to the indicator light.
    state_label: str = ""


PULL_FIRST = "Get the latest first."


def screen(
    status: Status,
    *,
    project_name: str,
    session_open: bool = False,
    last_push: dict | None = None,
) -> Screen:
    behind = status.versions_behind
    changed = status.has_local_changes

    return Screen(
        title=project_name,
        version_line=(
            f"Latest version: v{status.remote_version}      You have: v{status.local_version}"
        ),
        banner=_banner(status.state, behind),
        presence=describe_sessions(status.active_sessions),
        pull=Button(
            "Catch Up" if status.state == "push_blocked" else "Get Latest",
            enabled=behind > 0,
            reason="" if behind else "You have the latest version — nothing new to pull.",
        ),
        start=_session_button(session_open, behind),
        review=Button(
            "Review Changes",
            enabled=changed,
            reason="" if changed else "Nothing has changed since your last push.",
        ),
        push=_push_button(changed, behind),
        changes=_changes(status),
        summary=_summary(status),
        last_push=_last_push(last_push),
        primary=_primary(status.state, session_open),
        state_label=_state_label(status.state, behind, session_open),
    )


def _state_label(state: str, behind: int, session_open: bool) -> str:
    """Two or three words for the header. Being behind outranks everything."""
    if state == "behind":
        return f"{behind} VERSION{'S' if behind != 1 else ''} BEHIND"
    if state == "push_blocked":
        return "CATCH UP"
    if state == "local_changes":
        return "NOT PUT BACK"
    return "SESSION OPEN" if session_open else "UP TO DATE"


def _primary(state: str, session_open: bool) -> str:
    """The single next thing to do. Catching up always comes before publishing."""
    if state in ("behind", "push_blocked"):
        return "pull"
    if state == "local_changes":
        return "push"
    if not session_open:
        return "start"
    return ""


def _banner(state: str, behind: int) -> Banner:
    versions = f"{behind} version{'s' if behind != 1 else ''}"
    if state == "behind":
        return Banner(
            f"You are {versions} behind — someone put back while you were away. Get the latest before you start.", "warning"
        )
    if state == "push_blocked":
        return Banner(
            f"Someone put back {versions} while you were working. Catch Up saves your "
            "version first, then brings theirs — nothing is lost.",
            "danger",
        )
    if state == "local_changes":
        return Banner("You have work that has not been put back yet.", "info")
    return Banner("Up to date.", "ok")


def _session_button(session_open: bool, behind: int) -> Button:
    if session_open:
        return Button("Done Working", enabled=True)
    return Button(
        "Start Working",
        enabled=behind == 0,
        reason="" if behind == 0 else f"This project has moved on. {PULL_FIRST}",
    )


def _push_button(changed: bool, behind: int) -> Button:
    if behind:
        return Button("Put Back", enabled=False, reason=f"Someone put back first. {PULL_FIRST}")
    return Button(
        "Put Back",
        enabled=changed,
        reason="" if changed else "Nothing has changed since your last push.",
    )


def _changes(status: Status) -> list[tuple[str, str, str]]:
    diff = status.diff
    rows = []
    for path in diff.added:
        rows.append(("Added", path, human_size(diff.sizes.get(path, 0))))
    for path in diff.modified:
        rows.append(("Modified", path, human_size(diff.sizes.get(path, 0))))
    for path in diff.deleted:
        rows.append(("Removed", path, ""))
    return rows


def _summary(status: Status) -> str:
    if not status.has_local_changes:
        return ""
    return (
        f"Project: {human_size(status.project_bytes)}      "
        f"Already stored: {human_size(status.already_stored_bytes)}      "
        f"To upload: {human_size(status.upload_bytes)}"
    )


def _last_push(last_push: dict | None) -> str:
    if not last_push:
        return ""
    when = local_time(last_push.get("at"))
    note = last_push.get("note") or "(no note)"
    counts = (
        f"{last_push.get('added', 0)} added, "
        f"{last_push.get('modified', 0)} changed, "
        f"{last_push.get('deleted', 0)} removed"
    )
    return f"Last push: v{last_push.get('version')} — {when} — {note} ({counts})"


# --- closing a project ---------------------------------------------------


def close_warning(status: Status) -> str | None:
    """Why closing this project deserves a word first, or None.

    Closing never destroys anything — the files stay in the project folder —
    so this is a nudge that the work is not backed up and nobody else can see
    it, not a data-loss guard. Saying that plainly is the difference between a
    useful warning and a frightening one.
    """
    diff = status.diff
    if not diff.has_changes:
        return None

    count = len(diff.added) + len(diff.modified) + len(diff.deleted)
    files = "file has" if count == 1 else "files have"
    return (
        f"{count} {files} changed since your last push. Closing this does not "
        "delete them — they stay in the project folder — but they are not backed "
        "up, and nobody else can see them."
    )


# --- notifications -------------------------------------------------------


def notifications(before: Status | None, after: Status) -> list[str]:
    """Things worth interrupting someone for, comparing two refreshes.

    The first refresh never notifies: everything is new when you open the app,
    and being told so is noise.
    """
    if before is None:
        return []

    news = []
    if after.remote_version > before.remote_version:
        news.append(f"A new version (v{after.remote_version}) was pushed. Pull latest.")

    was = {s.get("key"): s for s in before.active_sessions}
    now = {s.get("key"): s for s in after.active_sessions}
    for key, session in now.items():
        who = session.get("display_name") or "Someone"
        if key not in was:
            news.append(f"{who} started working on this project.")
        elif session.get("state") == "pushing" and was[key].get("state") != "pushing":
            news.append(f"{who} is pushing right now.")
    for key, session in was.items():
        if key not in now:
            news.append(f"{session.get('display_name') or 'Someone'} finished working.")
    return news


# --- formatting ----------------------------------------------------------


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
