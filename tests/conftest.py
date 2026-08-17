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
