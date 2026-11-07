"""Knowing that someone else is working — before you start recording.

Each open session writes a small heartbeat file into the repository and
refreshes it while the app is running. It is deliberately advisory: it can tell
you Alice is tracking guitars right now, but it can never lock the project. A
client that crashes just stops beating, and its session ages out.
"""

from __future__ import annotations

import socket
import uuid
from datetime import datetime, timedelta, timezone

# A heartbeat is refreshed every minute; allow generous slack for a laptop that
# slept or a slow connection before we stop calling a session active.
HEARTBEAT_SECONDS = 60
ACTIVE_AFTER_SECONDS = 15 * 60
ABANDONED_AFTER_SECONDS = 24 * 60 * 60
# "I have changes I have not pushed" is a promise, not a heartbeat: it stays
# visible until the work is pushed or discarded (or this long has passed).
UNPUSHED_STAYS_FOR_SECONDS = 14 * 24 * 60 * 60


def new_session_key(author: dict) -> str:
    who = (author.get("email") or author.get("name") or "someone").replace("/", "_")
    return f"{who}-{uuid.uuid4().hex[:4]}"


def build_session(author: dict, base_version: int, key: str, state: str = "working") -> dict:
    now = _now()
    return {
        "key": key,
        "user": author.get("email"),
        "display_name": author.get("name") or author.get("email") or "Someone",
        "host": socket.gethostname(),
        "base_version": base_version,
        "state": state,
        "started_at": now,
        "last_heartbeat": now,
    }


def active_sessions(storage, *, exclude: str | None = None) -> list[dict]:
    """Sessions still beating, other people's only."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_AFTER_SECONDS)
    unpushed_cutoff = datetime.now(timezone.utc) - timedelta(seconds=UNPUSHED_STAYS_FOR_SECONDS)
    active = []
    for session in storage.list_sessions():
        if exclude and session.get("key") == exclude:
            continue
        beat = _parse(session.get("last_heartbeat"))
        if session.get("state") == "unpushed":
            if beat is not None and beat >= unpushed_cutoff:
                active.append(session)
        elif beat is not None and beat >= cutoff:
            active.append(session)
    return active


def is_working(session: dict) -> bool:
    """Someone at the keyboard right now, as opposed to leftover unpushed changes."""
    return session.get("state") in ("working", "pushing")


def abandoned_session_keys(storage) -> list[str]:
    """Sessions old enough that the client is certainly gone."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ABANDONED_AFTER_SECONDS)
    unpushed_cutoff = datetime.now(timezone.utc) - timedelta(seconds=UNPUSHED_STAYS_FOR_SECONDS)
    keys = []
    for session in storage.list_sessions():
        beat = _parse(session.get("last_heartbeat"))
        limit = unpushed_cutoff if session.get("state") == "unpushed" else cutoff
        if beat is None or beat < limit:
            if session.get("key"):
                keys.append(session["key"])
    return keys


def describe_sessions(sessions: list[dict]) -> str:
    """One plain-English line about who else is in the project."""
    lines = []
    for session in sessions:
        who = session.get("display_name") or "Someone"
        since = _friendly_time(session.get("started_at"))
        if session.get("state") == "unpushed":
            when = _friendly_day(session.get("unpushed_since") or session.get("started_at"))
            what = session.get("changed")
            lines.append(
                f"{who} has changes they have not pushed"
                + (f" since {when}" if when else "")
                + (f" ({what})." if what else ".")
            )
        elif session.get("state") == "pushing":
            lines.append(f"{who} is pushing right now — wait a moment before you push.")
        elif since:
            lines.append(f"{who} has been working since {since}.")
        else:
            lines.append(f"{who} is working right now.")
    return "\n".join(lines)


def _friendly_day(timestamp: str | None) -> str | None:
    parsed = _parse(timestamp)
    if parsed is None:
        return None
    return parsed.astimezone().strftime("%a %-I:%M %p")


def _friendly_time(timestamp: str | None) -> str | None:
    parsed = _parse(timestamp)
    if parsed is None:
        return None
    return parsed.astimezone().strftime("%-I:%M %p")


def _parse(timestamp: str | None):
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
