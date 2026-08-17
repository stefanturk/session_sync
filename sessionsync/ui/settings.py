"""What the app remembers between launches.

Only the projects you have opened. Nothing here is authoritative — deleting the
file loses your recent list and nothing else, so it is read defensively and
never allowed to stop the app starting.
"""

from __future__ import annotations

import json
from pathlib import Path

from sessionsync.auth.identity import config_home

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
