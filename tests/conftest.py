import gzip

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.repository import Repository
from sessionsync.storage.local_folder import LocalFolderStorage

ALICE = {"name": "Alice", "email": "alice@example.com"}
BOB = {"name": "Bob", "email": "bob@example.com"}


def write_project(root, name="My Song"):
    """A minimal but realistic Ableton project folder."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.als").write_bytes(gzip.compress(b"<Ableton MinorVersion='11.0_11300'/>"))
    (root / "Ableton Project Info").mkdir(exist_ok=True)
    (root / "Ableton Project Info" / "Project11.cfg").write_bytes(b"cfg")
    (root / "Samples" / "Recorded").mkdir(parents=True, exist_ok=True)
    (root / "Samples" / "Recorded" / "Vocal.wav").write_bytes(b"vocal audio")
    return root


@pytest.fixture
def remote(tmp_path):
    return LocalFolderStorage(tmp_path / "remote" / "My Song")


@pytest.fixture
def project(tmp_path):
    return write_project(tmp_path / "alice" / "My Song")


@pytest.fixture
def alice(project, remote):
    """A repository created from Alice's project folder, pushed as v1."""
    repo = Repository.create(project, remote, adapter=AbletonAdapter(), author=ALICE)
    repo.push(note="Initial version")
    return repo


@pytest.fixture
def bob(tmp_path, remote, alice):
    """Bob's working copy, pulled fresh from the same remote."""
    return Repository.clone(remote, tmp_path / "bob" / "My Song", author=BOB)


def als_with_tracks(tracks: dict[str, str]) -> bytes:
    """A gzipped Live Set with the given {track name: marker content}."""
    body = "".join(
        f'<MidiTrack Id="{i}"><Name><EffectiveName Value="{name}"/></Name>'
        f'<Notes Value="{marker}"/><TrackUnfolded Value="true"/></MidiTrack>'
        for i, (name, marker) in enumerate(tracks.items())
    )
    return gzip.compress(
        f"<Ableton MinorVersion='11.0_11300'><LiveSet><Tracks>{body}</Tracks></LiveSet></Ableton>".encode()
    )


@pytest.fixture(autouse=True)
def no_real_notifications(monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_NO_NOTIFY", "1")  # tray banners too
    """Tests must never pop real macOS banners ("My Song: Bob finished working").

    Everything that notifies goes through subprocess.run in ui.notify, so stub
    that one call. tests/test_ui_notify.py still checks the script text itself.
    """
    calls = []
    try:
        from sessionsync.ui import notify as notify_module
    except Exception:  # Qt-free environments
        return calls
    monkeypatch.setattr(notify_module.subprocess, "run", lambda *a, **k: calls.append(a))
    return calls
