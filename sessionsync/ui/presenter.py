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
    reason: str = ""  # why it is greyed out — never leave a user guessing
    hint: str = ""    # what it does, shown on hover whether or not it can be clicked

    @property
    def tooltip(self) -> str:
        if self.enabled or not self.reason:
            return self.hint
        return f"{self.reason}\n\n{self.hint}" if self.hint else self.reason


@dataclass
class Screen:
    title: str
    version_line: str
    banner: Banner
    presence: str
    open: Button
    pull: Button
    review: Button
    push: Button
    #: shown only when someone saved the song straight into Drive
    rescue: Button | None = None
    changes: list[tuple[str, str, str]] = field(default_factory=list)
    summary: str = ""
    last_push: str = ""
    #: name of the one button to draw attention to — "" when nothing is pressing.
    primary: str = ""
    #: short uppercase status for the header, next to the indicator light.
    state_label: str = ""
    #: a quiet line above the change list ("Only saved work counts…")
    changes_hint: str = ""
    #: hover text for the header's light and state
    state_hint: str = ""


PULL_FIRST = "Pull the latest first."

HINTS = {
    "open": "Opens YOUR copy of the song in Live — the one on this computer. "
            "Never open the files inside Google Drive.",
    "pull": "Brings in the newest version from the shared folder. Anything you "
            "changed and did not push is never replaced without asking.",
    "review": "Every file, and every track, that changed since the version you are on.",
    "push": "Saves your work as a new version for everyone: only the files the song "
            "uses are sent, and the set is kept in Past Revisions.",
    "rescue": "Brings the save made inside Google Drive into your own copy, and puts "
              "the shared copy back the way it was pushed. Nothing is thrown away.",
}

CHANGE_HINTS = {
    "Added": "New since the version you are on — Push sends it.",
    "Modified": "Changed since the version you are on — Push sends the new version.",
    "Removed": "Gone from your copy. The shared copy keeps it for older revisions.",
}


def screen(
    status: Status,
    *,
    project_name: str,
    session_open: bool = False,  # kept for older callers; working is automatic now
    last_push: dict | None = None,
    live_open: bool = False,
    live_on_drive: bool = False,
    set_summary: str = "",
) -> Screen:
    behind = status.versions_behind
    changed = status.has_local_changes
    edits = list(getattr(status, "drive_edits", []) or [])
    live_open = live_open or session_open

    return Screen(
        title=project_name,
        version_line=(
            f"Latest version: v{status.remote_version}      You have: v{status.local_version}"
        ),
        banner=_banner(status.state, behind, live_open=live_open, live_on_drive=live_on_drive,
                       edits=bool(edits)),
        presence=describe_sessions(status.active_sessions),
        open=Button("Open in Live", enabled=True, hint=HINTS["open"]),
        pull=Button(
            "Catch Up" if status.state == "push_blocked" else "Pull",
            enabled=behind > 0,
            reason="" if behind else "You have the latest version — nothing new to pull.",
            hint=HINTS["pull"],
        ),
        review=Button(
            "Review Changes",
            enabled=changed,
            reason="" if changed else "Nothing has changed since the version you are on.",
            hint=HINTS["review"],
        ),
        push=_push_button(changed, behind, edits=bool(edits)),
        rescue=Button("Rescue Edits", enabled=True, hint=HINTS["rescue"]) if edits else None,
        changes=_changes(status, set_summary),
        summary=_summary(status),
        last_push=_last_push(last_push),
        primary=_primary(status.state, edits=bool(edits)),
        state_label=_state_label(status.state, behind, live_open=live_open, edits=bool(edits)),
        changes_hint=(
            "Only saved work counts — press ⌘S in Live before you push."
            if live_open else ""
        ),
        state_hint=_state_hint(status.state, behind),
    )


def _state_label(state: str, behind: int, *, live_open: bool = False, edits: bool = False) -> str:
    """Two or three words for the header. Being behind outranks everything."""
    if edits:
        return "RESCUE NEEDED"
    if state == "behind":
        return f"{behind} VERSION{'S' if behind != 1 else ''} BEHIND"
    if state == "push_blocked":
        return "CATCH UP"
    if state == "local_changes":
        return "NOT PUSHED"
    return "OPEN IN LIVE" if live_open else "UP TO DATE"


def _state_hint(state: str, behind: int) -> str:
    if state in ("behind", "push_blocked"):
        return f"Someone pushed {behind} newer version{'s' if behind != 1 else ''} since yours."
    if state == "local_changes":
        return "Your copy has saved changes nobody else has yet. Push to share them."
    return "Your copy matches the newest version in the shared folder."


def _primary(state: str, *, edits: bool = False) -> str:
    """The single next thing to do. Catching up always comes before publishing."""
    if edits:
        return "rescue"
    if state in ("behind", "push_blocked"):
        return "pull"
    if state == "local_changes":
        return "push"
    return "open"


def _banner(state: str, behind: int, *, live_open: bool = False, live_on_drive: bool = False,
            edits: bool = False) -> Banner:
    versions = f"{behind} version{'s' if behind != 1 else ''}"
    if live_on_drive:
        return Banner(
            "Live has the SHARED copy in Google Drive open. Close it in Live without "
            "saving, then use Open in Live — that opens your own copy.",
            "danger",
        )
    if edits:
        return Banner(
            "Someone saved this song directly inside the shared folder in Google Drive. "
            "Rescue Edits brings that save into your copy — nothing is lost.",
            "danger",
        )
    if state == "behind" and live_open:
        return Banner(
            f"Live has this song open, but you are {versions} behind. Don't save — close "
            "it in Live, Pull, then open it again.",
            "danger",
        )
    if state == "behind":
        return Banner(
            f"You are {versions} behind — someone pushed while you were away. Pull the "
            "latest before you start.",
            "warning",
        )
    if state == "push_blocked":
        return Banner(
            f"Someone pushed {versions} while you were working. Catch Up saves your "
            "version first, then brings theirs — nothing is lost.",
            "danger",
        )
    if state == "local_changes":
        return Banner("You have work that has not been pushed yet.", "info")
    if live_open:
        return Banner("Up to date — the song is open in Live.", "ok")
    return Banner("Up to date.", "ok")


def _push_button(changed: bool, behind: int, *, edits: bool = False) -> Button:
    if edits:
        return Button("Push", enabled=False, reason="Rescue the edit made in Drive first.",
                      hint=HINTS["push"])
    if behind:
        return Button("Push", enabled=False, reason=f"Someone pushed first. {PULL_FIRST}",
                      hint=HINTS["push"])
    return Button(
        "Push",
        enabled=changed,
        reason="" if changed else "Nothing has changed since the version you are on.",
        hint=HINTS["push"],
    )


def _changes(status: Status, set_summary: str = "") -> list[tuple[str, str, str]]:
    diff = status.diff

    def label(path: str) -> str:
        # "My Song.als — Changed Main, Drums": say what changed inside the set
        if set_summary and path.lower().endswith(".als") and "/" not in path:
            return f"{path}  —  {set_summary}"
        return path

    rows = []
    for path in diff.added:
        rows.append(("Added", label(path), human_size(diff.sizes.get(path, 0))))
    for path in diff.modified:
        rows.append(("Modified", label(path), human_size(diff.sizes.get(path, 0))))
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
