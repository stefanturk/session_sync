"""What the app remembers between launches.

The projects you have opened, the collaborator folders (hubs) you connected, and
which versions you have already been told about. Nothing here is authoritative — deleting the
file loses your recent list and nothing else, so it is read defensively and
never allowed to stop the app starting.
"""

from __future__ import annotations

import json
from pathlib import Path

from sessionsync.auth.identity import config_home
from sessionsync.core.hub import Hub, hub_to_json

SETTINGS_FILE = "ui.json"
MAX_RECENT = 10


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_home() / SETTINGS_FILE

    def recent_projects(self) -> list[Path]:
        """Most recent first, skipping any folder that has since gone."""
        paths = [Path(p) for p in self._read().get("recent_projects", [])]
        return [p for p in paths if p.is_dir()]

    def last_project(self) -> Path | None:
        recent = self.recent_projects()
        return recent[0] if recent else None

    def remember(self, project: Path) -> None:
        project = Path(project).expanduser().resolve()
        recent = [str(project)]
        recent += [str(p) for p in self.recent_projects() if p != project]
        self._write({**self._read(), "recent_projects": recent[:MAX_RECENT]})

    def forget(self, project: Path) -> None:
        project = Path(project).expanduser().resolve()
        recent = [str(p) for p in self.recent_projects() if p != project]
        self._write({**self._read(), "recent_projects": recent})

    # --- hubs (shared collaborator folders) ---------------------------------

    def hubs(self) -> list[Hub]:
        out = []
        for entry in self._read().get("hubs", []):
            if isinstance(entry, dict) and entry.get("path"):
                out.append(Hub(Path(entry["path"]), entry.get("name")))
        return out

    def add_hub(self, path: Path, name: str | None = None) -> Hub:
        hub = Hub(Path(path).expanduser(), name)
        kept = [h for h in self.hubs() if h.path != hub.path]
        self._write({**self._read(), "hubs": [hub_to_json(h) for h in kept + [hub]]})
        return hub

    def remove_hub(self, hub: Hub) -> None:
        kept = [h for h in self.hubs() if h.path != hub.path]
        data = self._read()
        data["hubs"] = [hub_to_json(h) for h in kept]
        data.get("seen", {}).pop(str(hub.path), None)
        self._write(data)

    def local_root(self) -> Path:
        """Where new songs go on this computer: the Desktop unless you chose somewhere.

        (Not a Desktop that iCloud syncs — see core/locations.py.)
        """
        from sessionsync.core import locations

        chosen = self._read().get("local_root")
        return Path(chosen) if chosen else locations.default_root()

    def local_root_chosen(self) -> bool:
        return bool(self._read().get("local_root"))

    def songs(self) -> list[tuple[str, Path]]:
        """("hub|song", where your copy is) for every song this computer has joined or shared."""
        return [(key, Path(path)) for key, path in self._read().get("song_paths", {}).items()]

    def song_by_repo(self, repository_id: str) -> Path | None:
        """Your copy of the song with this repository id, if it is still there."""
        from sessionsync.core.localstate import LocalState

        for _, path in self.songs():
            if not LocalState.exists(path):
                continue
            try:
                state = LocalState.open(path)
                try:
                    if state.repository_id == repository_id:
                        return path
                finally:
                    state.close()
            except Exception:  # noqa: BLE001 - an unreadable copy is just not this one
                continue
        return None

    def recorded_song_path(self, repository_id: str) -> Path | None:
        """Where your copy was, even if it is gone now (for "your copy isn't there any more")."""
        found = self._read().get("song_repos", {}).get(repository_id)
        return Path(found) if found else None

    def live_major(self) -> int | None:
        """The Live version chosen in Settings (None: the newest one installed)."""
        value = self._read().get("live_major")
        return int(value) if value else None

    def set_live_major(self, major: int | None) -> None:
        from sessionsync.adapters.ableton import versions

        self._write({**self._read(), "live_major": major})
        versions.set_preferred_major(major)

    def flag(self, name: str) -> bool:
        return bool(self._read().get("flags", {}).get(name))

    def set_flag(self, name: str, value: bool = True) -> None:
        data = self._read()
        data.setdefault("flags", {})[name] = value
        self._write(data)

    def activity(self) -> list:
        """The feed of what collaborators did, newest first (see ui/activity.py)."""
        from sessionsync.ui import activity

        return activity.load(self._read().get("activity"))

    def add_activity(self, item) -> None:
        from sessionsync.ui import activity

        data = self._read()
        data["activity"] = activity.dump(activity.add(activity.load(data.get("activity")), item))
        self._write(data)

    def mark_pulled(self, local: Path) -> None:
        """Your copy caught up: the feed's "Pull" links for that song are done."""
        data = self._read()
        changed = False
        for entry in data.get("activity") or []:
            if entry.get("local") == str(local) and entry.get("kind") == "pushed" and not entry.get("pulled"):
                entry["pulled"] = True
                changed = True
        if changed:
            self._write(data)

    def set_local_root(self, path: Path) -> None:
        self._write({**self._read(), "local_root": str(path)})

    def song_path(self, hub: Hub, song: str) -> Path:
        """Where this song lives on this computer: recorded when joined or shared,
        otherwise the standard place under the local root."""
        recorded = self._read().get("song_paths", {}).get(f"{hub.path}|{song}")
        return Path(recorded) if recorded else hub.local_path(song, self.local_root())

    def set_song_path(self, hub: Hub, song: str, path: Path, repository_id: str | None = None) -> None:
        data = self._read()
        data.setdefault("song_paths", {})[f"{hub.path}|{song}"] = str(path)
        if repository_id:
            data.setdefault("song_repos", {})[repository_id] = str(path)
        self._write(data)

    def hidden_songs(self, hub: Hub) -> list[str]:
        """Songs removed from Session Sync on this computer: not listed, no news."""
        return list(self._read().get("hidden_songs", {}).get(str(hub.path), []))

    def hide_song(self, hub: Hub, song: str) -> None:
        data = self._read()
        hidden = data.setdefault("hidden_songs", {}).setdefault(str(hub.path), [])
        if song not in hidden:
            hidden.append(song)
        path = data.get("song_paths", {}).pop(f"{hub.path}|{song}", None)
        if path:
            repos = data.get("song_repos", {})
            for key in [k for k, v in repos.items() if v == path]:
                repos.pop(key)
        self._write(data)

    def unhide_song(self, hub: Hub, song: str) -> None:
        data = self._read()
        hidden = data.get("hidden_songs", {}).get(str(hub.path), [])
        if song in hidden:
            hidden.remove(song)
            self._write(data)

    def forget_song_path(self, path: Path) -> None:
        """A song folder that is no longer tracked here: out of every list."""
        path = str(Path(path))
        data = self._read()
        for table in ("song_paths", "song_repos"):
            entries = data.get(table, {})
            for key in [k for k, v in entries.items() if v == path]:
                entries.pop(key)
        data["recent_projects"] = [p for p in data.get("recent_projects", []) if p != path]
        self._write(data)

    def seen(self, hub: Hub) -> dict[str, int] | None:
        """{song: newest version already reported}, or None for a hub never scanned."""
        value = self._read().get("seen", {}).get(str(hub.path))
        return value if isinstance(value, dict) else None

    def set_seen(self, hub: Hub, seen: dict[str, int]) -> None:
        data = self._read()
        data.setdefault("seen", {})[str(hub.path)] = seen
        self._write(data)

    # --- storage ---------------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
