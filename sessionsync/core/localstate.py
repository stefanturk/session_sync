"""What this Mac knows about its working copy.

Lives in `.sessionsync/` inside the project folder, so the folder stays an
ordinary Ableton project that opens fine without SessionSync installed.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

STATE_DIR = ".sessionsync"
STATE_DB = "state.db"
TRASH_DIR = "trash"


class LocalState:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)"
        )
        self._conn.commit()

    # --- locations -----------------------------------------------------

    @property
    def dir(self) -> Path:
        return self.project_root / STATE_DIR

    @property
    def db_path(self) -> Path:
        return self.dir / STATE_DB

    @property
    def trash_dir(self) -> Path:
        return self.dir / TRASH_DIR

    # --- lifecycle -----------------------------------------------------

    @classmethod
    def create(cls, project_root: Path, *, repository_id: str, remote: str) -> LocalState:
        (Path(project_root) / STATE_DIR).mkdir(parents=True, exist_ok=True)
        state = cls(project_root)
        state.repository_id = repository_id
        state.remote = remote
        return state

    @classmethod
    def open(cls, project_root: Path) -> LocalState:
        db = Path(project_root) / STATE_DIR / STATE_DB
        if not db.exists():
            raise FileNotFoundError(
                f"{project_root} is not set up with SessionSync yet."
            )
        return cls(project_root)

    @classmethod
    def exists(cls, project_root: Path) -> bool:
        return (Path(project_root) / STATE_DIR / STATE_DB).exists()

    # --- stored values -------------------------------------------------

    def _get(self, key: str, default=None):
        row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key: str, value) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        self._conn.commit()

    @property
    def repository_id(self) -> str | None:
        return self._get("repository_id")

    @repository_id.setter
    def repository_id(self, value: str) -> None:
        self._set("repository_id", value)

    @property
    def remote(self) -> str | None:
        return self._get("remote")

    @remote.setter
    def remote(self, value: str) -> None:
        self._set("remote", value)

    @property
    def base_version(self) -> int:
        return self._get("base_version", 0)

    @base_version.setter
    def base_version(self, value: int) -> None:
        self._set("base_version", value)

    @property
    def baseline(self) -> dict | None:
        """File map captured when the current session started."""
        return self._get("baseline")

    @baseline.setter
    def baseline(self, value: dict | None) -> None:
        self._set("baseline", value)

    @property
    def session(self) -> dict | None:
        return self._get("session")

    @session.setter
    def session(self, value: dict | None) -> None:
        self._set("session", value)

    @property
    def seen_changes(self) -> str | None:
        """Fingerprint of the unpushed changes the app last showed, to spot ones made while it was closed."""
        return self._get("seen_changes")

    @seen_changes.setter
    def seen_changes(self, value: str | None) -> None:
        self._set("seen_changes", value)

    @property
    def last_push(self) -> dict | None:
        return self._get("last_push")

    @last_push.setter
    def last_push(self, value: dict | None) -> None:
        self._set("last_push", value)

    def close(self) -> None:
        self._conn.close()
