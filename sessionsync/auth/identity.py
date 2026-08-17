"""Who is pushing.

Until Google sign-in arrives this is just a name and an email kept on this Mac;
it is what shows up next to a version in the project's history. The Google
account will replace it as the source of truth, and the shape stays the same.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_HOME = Path.home() / ".sessionsync"
IDENTITY_FILE = "identity.json"


class IdentityNotSetError(Exception):
    def __init__(self):
        super().__init__(
            "SessionSync does not know who you are yet. "
            "Run: sessionsync identity \"Your Name\" you@example.com"
        )


def config_home() -> Path:
    return Path(os.environ.get("SESSIONSYNC_HOME", DEFAULT_HOME))


def save_identity(name: str, email: str) -> dict:
    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    identity = {"name": name, "email": email}
    (home / IDENTITY_FILE).write_text(
        json.dumps(identity, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return identity


def load_identity() -> dict:
    path = config_home() / IDENTITY_FILE
    identity = {}
    if path.exists():
        identity = json.loads(path.read_text(encoding="utf-8"))

    identity = {
        "name": os.environ.get("SESSIONSYNC_NAME") or identity.get("name"),
        "email": os.environ.get("SESSIONSYNC_EMAIL") or identity.get("email"),
    }
    if not identity["name"] and not identity["email"]:
        raise IdentityNotSetError()
    return identity
