import gzip

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.repository import Repository
from sessionsync.storage.local_folder import LocalFolderStorage

ALICE = {"name": "Alice", "email": "alice@example.com"}
BOB = {"name": "Bob", "email": "bob@example.com"}


VOCAL = "Samples/Recorded/Vocal.wav"


def sample_ref(rel: str) -> str:
    """A <SampleRef> the way Live 11 writes one for audio inside the project."""
    return (
        "<SampleRef><FileRef>"
        '<RelativePathType Value="3" />'
        f'<RelativePath Value="{rel}" />'
        f'<Path Value="/Users/someone/Music/My Song/{rel}" />'
        '<Type Value="2" /><LivePackName Value="" /><LivePackId Value="" />'
        "</FileRef></SampleRef>"
    )


def live_set(body: str = "", samples=(VOCAL,)) -> bytes:
    """A gzipped Live 11 Set that uses `samples` (project-relative paths)."""
    refs = "".join(sample_ref(s) for s in samples)
    xml = f'<Ableton MinorVersion="11.0_11300"><LiveSet>{body}</LiveSet>{refs}</Ableton>'
    return gzip.compress(xml.encode("utf-8"), mtime=0)


def als_marked(marker: bytes | str, samples=(VOCAL,)) -> bytes:
    """A Live Set whose content differs by `marker`: "Alice's arrangement"."""
    if isinstance(marker, bytes):
        marker = marker.decode()
    return live_set(f'<Annotation Value="{marker}" />', samples)


def use_sample(project, rel: str, set_name: str | None = None) -> None:
    """Make the project's Live Set use `rel`, as dragging it into Live would."""
    sets = [project / set_name] if set_name else sorted(project.glob("*.als"))
    target = sets[0]
    xml = gzip.decompress(target.read_bytes())
    if f'Value="{rel}"'.encode() in xml:
        return
    xml = xml.replace(b"</Ableton>", sample_ref(rel).encode() + b"</Ableton>")
    target.write_bytes(gzip.compress(xml, mtime=0))


def record(project, rel: str, data: bytes, set_name: str | None = None):
    """A new recording: the audio file plus the Live Set now using it."""
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    use_sample(project, rel, set_name)
    return path


def write_project(root, name="My Song"):
    """A minimal but realistic Ableton project folder."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.als").write_bytes(live_set())
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


def als_with_tracks(tracks: dict[str, str], samples=(VOCAL,)) -> bytes:
    """A gzipped Live Set with the given {track name: marker content}."""
    body = "".join(
        f'<MidiTrack Id="{i}"><Name><EffectiveName Value="{name}"/></Name>'
        f'<Notes Value="{marker}"/><TrackUnfolded Value="true"/></MidiTrack>'
        for i, (name, marker) in enumerate(tracks.items())
    )
    refs = "".join(sample_ref(s) for s in samples)
    return gzip.compress(
        f"<Ableton MinorVersion='11.0_11300'><LiveSet><Tracks>{body}</Tracks></LiveSet>{refs}</Ableton>".encode(),
        mtime=0,
    )


@pytest.fixture(autouse=True)
def one_live_version(monkeypatch):
    """Everyone in a test uses Live 11 and keeps sets exactly as shared, unless
    the test is about Live versions (tests/test_live_versions.py)."""
    from sessionsync.adapters.ableton import versions

    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "11")
    monkeypatch.setattr(versions, "LOCALIZE", False)


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch, tmp_path):
    """New songs default to the Desktop: in tests that is a pretend one."""
    user = tmp_path / "user-home"
    (user / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("SESSIONSYNC_USER_HOME", str(user))
    return user


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
    import types

    stub = types.SimpleNamespace(
        run=lambda *a, **k: calls.append(a), SubprocessError=notify_module.subprocess.SubprocessError
    )
    monkeypatch.setattr(notify_module, "subprocess", stub)  # only this module; others keep the real one
    return calls
