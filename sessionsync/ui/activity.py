"""What your collaborators have been doing: a short feed, newest first.

Kept on this computer only (in the app's settings, the last 50 lines), built
from what the app already sees: pushes and new songs from the shared folders,
and who started or stopped working. Every line says who did what to which song,
and knows where clicking it should take you. No Qt in here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

KEEP = 50
SHOWN = 10


@dataclass
class Activity:
    text: str
    song: str = ""
    kind: str = "other"  # "pushed" | "new_song" | "working" | "other"
    who: str = ""
    versions: list = field(default_factory=list)
    hub: str | None = None    # the shared folder's path
    local: str | None = None  # the song's folder on this computer
    pulled: bool = False
    at: str = ""

    def __post_init__(self):
        if not self.at:
            self.at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def key(self) -> tuple:
        if self.kind == "pushed" and self.versions:
            return ("pushed", self.song, self.versions[-1])
        return (self.kind, self.song, self.text)

    def links(self) -> list[tuple[str, str]]:
        """[(label, action)] for this line: where clicking can take you."""
        if self.kind == "new_song":
            return [("Get my copy", "open")]
        if self.kind == "pushed":
            if self.local and not self.pulled:
                return [("Pull", "pull"), ("See changes", "history")]
            if self.local:
                return [("Open song", "open"), ("See changes", "history")]
            return [("Get my copy", "open")]
        if self.song:
            return [("Open song", "open")]
        return []

    @classmethod
    def from_news(cls, news) -> "Activity":
        return cls(
            text=news.text,
            song=news.song,
            kind=getattr(news, "kind", "other"),
            who=getattr(news, "who", ""),
            versions=list(getattr(news, "versions", []) or []),
            hub=str(news.hub.path) if getattr(news, "hub", None) is not None else None,
            local=str(news.path) if news.path else None,
            pulled=bool(getattr(news, "pulled", False)),
        )


def load(raw) -> list[Activity]:
    items = []
    for entry in raw or []:
        try:
            items.append(Activity(**entry))
        except TypeError:
            continue
    return items


def add(items: list[Activity], new: Activity) -> list[Activity]:
    """Newest first; the same push told twice (banner and hub check) is kept once."""
    kept = [i for i in items if i.key != new.key]
    return [new] + kept[: KEEP - 1]


def dump(items: list[Activity]) -> list[dict]:
    return [asdict(i) for i in items[:KEEP]]


def day_label(at: str, now: datetime | None = None) -> str:
    try:
        when = datetime.fromisoformat(at).astimezone()
    except ValueError:
        return ""
    today = (now or datetime.now().astimezone()).date()
    if when.date() == today:
        return "Today"
    if when.date() == today - timedelta(days=1):
        return "Yesterday"
    return when.strftime("%a %d %b")


def time_label(at: str) -> str:
    try:
        when = datetime.fromisoformat(at).astimezone()
    except ValueError:
        return ""
    return when.strftime("%H:%M")


def grouped(items: list[Activity], limit: int = SHOWN) -> list[tuple[str, list[Activity]]]:
    """[(day, lines)] for the newest `limit` lines."""
    groups: list[tuple[str, list[Activity]]] = []
    for item in items[:limit]:
        day = day_label(item.at)
        if groups and groups[-1][0] == day:
            groups[-1][1].append(item)
        else:
            groups.append((day, [item]))
    return groups
