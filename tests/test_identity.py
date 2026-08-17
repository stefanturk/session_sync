import json

import pytest

from sessionsync.auth.identity import (
    IdentityNotSetError,
    load_identity,
    save_identity,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SESSIONSYNC_NAME", raising=False)
    monkeypatch.delenv("SESSIONSYNC_EMAIL", raising=False)


def test_saved_identity_is_remembered():
    save_identity("Alice", "alice@example.com")

    assert load_identity() == {"name": "Alice", "email": "alice@example.com"}


def test_asking_for_an_identity_that_was_never_set_raises():
    with pytest.raises(IdentityNotSetError):
        load_identity()


def test_the_environment_overrides_the_saved_identity(monkeypatch):
    save_identity("Alice", "alice@example.com")
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice on the studio Mac")

    assert load_identity()["name"] == "Alice on the studio Mac"


def test_the_identity_file_is_readable_by_a_human(tmp_path):
    save_identity("Alice", "alice@example.com")

    stored = json.loads((tmp_path / "config" / "identity.json").read_text())

    assert stored["email"] == "alice@example.com"
