"""Live 10, 11 and 12 on one song.

The shared copy stays in the song's Live version; each computer gets its sets in
its own Live version, with samples found on that computer; a Push turns them back.
"""

import gzip
import xml.etree.ElementTree as ET
from collections import Counter

import pytest

from sessionsync.adapters.ableton import versions
from sessionsync.core.repository import Repository

from tests.conftest import ALICE, BOB, VOCAL, sample_ref

# --- small but real-shaped sets ----------------------------------------------------


def live11(marker="start", samples=(VOCAL,)):
    refs = "".join(sample_ref(s) for s in samples)
    xml = (
        '<Ableton MajorVersion="5" MinorVersion="11.0_11300" Creator="Ableton Live 11.3.13">'
        '<LiveSet><NextPointeeId Value="10" />'
        '<MasterTrack><Name Value="Main" /><Oversampling Value="0" />'
        '<Volume><AutomationTarget Id="5"><LockEnvelope Value="0" /></AutomationTarget></Volume>'
        "</MasterTrack>"
        f'<Annotation Value="{marker}" /><ViewStateSesstionTrackWidth Value="93" />'
        f"</LiveSet>{refs}</Ableton>"
    )
    return gzip.compress(xml.encode(), mtime=0)


def live12(marker="start", samples=(VOCAL,), windows=False):
    refs = "".join(sample_ref(s) for s in samples)
    if windows:
        refs = refs.replace('<Type Value="2" />', '<Type Value="1" />').replace(
            "/Users/someone/Music/My Song/", "C:/Users/sam/Music/My Song/"
        )
    refs = refs.replace("</FileRef>", '<SourceHint Value="" /></FileRef>')
    xml = (
        '<Ableton MajorVersion="5" MinorVersion="12.0_12300" Creator="Ableton Live 12.3.2">'
        '<LiveSet><NextPointeeId Value="10" />'
        '<MainTrack SelectedToolPanel="7"><Name Value="Main" /><Oversampling Value="false" />'
        '<Volume><AutomationTarget Id="5"><LockEnvelope Value="0" /></AutomationTarget></Volume>'
        '<MpePitchBendUsesTuning Value="true" /></MainTrack>'
        f'<Annotation Value="{marker}" /><TuningSystems><TuningSystem><Name Value="Just" /></TuningSystem></TuningSystems>'
        f"</LiveSet>{refs}</Ableton>"
    )
    return gzip.compress(xml.encode(), mtime=0)


def root_of(data):
    return ET.fromstring(gzip.decompress(data))


def marker(path):
    return root_of(path.read_bytes()).find("LiveSet/Annotation").get("Value")


@pytest.fixture
def schemas(monkeypatch):
    """Schemas learned from the sets above, standing in for the shipped ones."""
    s11 = versions.Schema(major=11)
    s11.learn(root_of(live11()))
    s12 = versions.Schema(major=12)
    s12.learn(root_of(live12()))
    known = {11: s11.finish(), 12: s12.finish()}
    monkeypatch.setattr(versions, "schema_for", lambda major: known.get(major))
    monkeypatch.setattr(versions, "LOCALIZE", True)
    return known


# --- converting ---------------------------------------------------------------------


def test_a_live12_set_becomes_one_live11_opens(schemas):
    out = versions.convert(live12("Sam's take"), 11)
    root = root_of(out.data)
    assert root.get("MinorVersion") == "11.0_11300"
    assert root.get("Creator") == "Ableton Live 11.3.13"
    assert root.find("LiveSet/MasterTrack/Name").get("Value") == "Main"  # renamed back
    assert root.find("LiveSet/MainTrack") is None
    assert root.find("LiveSet/Annotation").get("Value") == "Sam's take"  # the work is kept
    assert root.find("LiveSet/MasterTrack").get("SelectedToolPanel") is None  # Live 12 only
    assert root.find("LiveSet/MasterTrack/MpePitchBendUsesTuning") is None
    assert root.find("LiveSet/MasterTrack/Oversampling").get("Value") == "0"  # was true/false
    assert root.find("LiveSet/ViewStateSesstionTrackWidth") is not None  # Live 11 wants it
    assert "a tuning system" in out.lost
    assert all(fr.find("SourceHint") is None for fr in root.iter("FileRef"))


def test_settings_added_for_the_older_live_get_their_own_ids(schemas):
    s11 = schemas[11]
    s11.order[("Ableton", "LiveSet", "MasterTrack")] += ("Pan",)
    s11.counts[("Ableton", "LiveSet", "MasterTrack", "Pan")] = 1
    s11.templates[("Ableton", "LiveSet", "MasterTrack", "Pan")] = (
        b'<Pan><AutomationTarget Id="5"><LockEnvelope Value="0" /></AutomationTarget></Pan>'
    )
    s11.counts[("Ableton", "LiveSet", "MasterTrack", "Pan", "AutomationTarget")] = 1
    s11.counts[("Ableton", "LiveSet", "MasterTrack", "Pan", "AutomationTarget", "LockEnvelope")] = 1
    root = root_of(versions.convert(live12(), 11).data)
    ids = Counter(x.get("Id") for x in root.iter() if x.tag.endswith("Target"))
    assert max(ids.values()) == 1
    assert int(root.find("LiveSet/NextPointeeId").get("Value")) > max(int(i) for i in ids)


def test_a_set_already_in_that_version_is_left_alone(schemas):
    data = live11()
    assert versions.convert(data, 11).data == data


def test_live11_to_live10_writes_live10_sample_references():
    root = ET.fromstring(
        f'<Ableton MinorVersion="11.0_11300"><LiveSet />{sample_ref("Samples/Recorded/Vocal.wav")}</Ableton>'
    )
    versions._filerefs_to_live10(root)
    fr = root.find("SampleRef/FileRef")
    assert fr.find("HasRelativePath").get("Value") == "true"
    assert [e.get("Dir") for e in fr.find("RelativePath")] == ["Samples", "Recorded"]
    assert fr.find("Name").get("Value") == "Vocal.wav"
    assert fr.find("Path") is None


def test_the_shipped_schemas_load():
    for major in (10, 11):
        schema = versions.Schema.from_json((versions.SCHEMA_DIR / f"live{major}.json.gz").read_bytes())
        assert schema.header["MinorVersion"].startswith(f"{major}.")
        assert ("Ableton", "LiveSet", "Tracks") in schema.counts


# --- samples found on this computer -------------------------------------------------


def test_a_windows_set_finds_its_samples_on_a_mac(tmp_path):
    project = tmp_path / "My Song"
    out = versions.localize(live12(windows=True), project / "My Song.als", project, windows=False)
    fr = root_of(out).find("SampleRef/FileRef")
    assert fr.find("Type").get("Value") == versions.MAC_PATH
    assert fr.find("Path").get("Value") == str(project / "Samples" / "Recorded" / "Vocal.wav")


def test_a_mac_set_finds_its_samples_on_windows(tmp_path):
    project = tmp_path / "My Song"
    out = versions.localize(live11(), project / "My Song.als", project, windows=True)
    fr = root_of(out).find("SampleRef/FileRef")
    assert fr.find("Type").get("Value") == versions.WINDOWS_PATH
    assert "\\" not in fr.find("Path").get("Value")


# --- a song shared between Live 11 and Live 12 ---------------------------------------


@pytest.fixture
def song(tmp_path, remote, schemas, monkeypatch):
    """Alice (Live 11) shares a Live 11 song; Bob (Live 12) gets his copy."""
    from sessionsync.adapters.ableton.adapter import AbletonAdapter

    alice_dir = tmp_path / "alice" / "My Song"
    (alice_dir / "Samples" / "Recorded").mkdir(parents=True)
    (alice_dir / "Samples" / "Recorded" / "Vocal.wav").write_bytes(b"vocal audio")
    (alice_dir / "My Song.als").write_bytes(live11("Alice v1"))
    alice = Repository.create(alice_dir, remote, adapter=AbletonAdapter(), author=ALICE)
    alice.push(note="first")
    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "12")
    bob = Repository.clone(remote, tmp_path / "bob" / "My Song", author=BOB)
    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "11")
    return alice, bob


def as_bob(monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "12")


def test_live12_gets_the_songs_set_which_it_opens_itself(song, monkeypatch):
    alice, bob = song
    as_bob(monkeypatch)
    bob_set = bob.project_root / "My Song.als"
    assert versions.set_major(bob_set) == 11  # Live 12 opens it; nothing is made up
    assert marker(bob_set) == "Alice v1"
    assert not bob.review().has_changes


def test_live10_gets_a_live10_set(tmp_path, song, remote, monkeypatch):
    shaped10 = root_of(live11())
    versions._filerefs_to_live10(shaped10)  # Live 10 keeps sample paths as folder elements
    s10 = versions.Schema(major=10)
    s10.learn(shaped10)
    s10.header = {"MinorVersion": "10.0_377", "Creator": "Ableton Live 10.1"}
    schemas = {11: versions.schema_for(11), 10: s10.finish()}
    monkeypatch.setattr(versions, "schema_for", lambda major: schemas.get(major))
    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "10")
    carol = Repository.clone(remote, tmp_path / "carol" / "My Song", author=BOB)
    assert versions.set_major(carol.project_root / "My Song.als") == 10
    assert not carol.review().has_changes


def test_a_live12_push_is_kept_as_live11_and_opens_for_live11(song, monkeypatch):
    alice, bob = song
    as_bob(monkeypatch)
    (bob.project_root / "My Song.als").write_bytes(live12("Bob's bass"))
    bob.push(note="bass")
    assert not bob.review().has_changes  # his Live 12 set is what he pushed

    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "11")
    alice.pull()
    alice_set = alice.project_root / "My Song.als"
    assert versions.set_major(alice_set) == 11
    assert marker(alice_set) == "Bob's bass"
    assert not alice.review().has_changes
    assert alice.song_live_major() == 11
    assert alice.storage.read_repo_metadata()["live_major"] == 11


def test_live12s_save_as_new_set_becomes_the_song_again(song, monkeypatch):
    alice, bob = song
    as_bob(monkeypatch)
    # Live 12 asked to save as a new set, and Bob said yes: the work is in "My Song 12".
    (bob.project_root / "My Song 12.als").write_bytes(live12("Bob in Live 12"))
    assert bob.saved_as_new() == [("My Song 12.als", "My Song.als")]
    bob.push(note="vocals")
    shared = bob.read_manifest(bob.base_version).files
    assert "My Song 12.als" not in shared
    assert not (bob.project_root / "My Song 12.als").exists()
    assert versions.set_major(bob.project_root / "My Song.als") == 12  # he keeps working in 12
    assert marker(bob.project_root / "My Song.als") == "Bob in Live 12"
    assert not bob.review().has_changes

    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "11")
    alice.pull()
    assert marker(alice.project_root / "My Song.als") == "Bob in Live 12"
    assert versions.set_major(alice.project_root / "My Song.als") == 11


def test_the_live12_set_already_shared_beside_the_song_is_merged_from_a_live11_copy(song, monkeypatch):
    """What happened on 7 Nov: Sam pushed "My Song 12.als" beside the song's set."""
    alice, bob = song
    as_bob(monkeypatch)
    (bob.project_root / "My Song 12.als").write_bytes(live12("Sam's 12 work"))
    with monkeypatch.context() as old_app:  # the old app pushed it as it was
        old_app.setattr(Repository, "_in_song_version", lambda self, f, s, scratch: (f, s, []))
        bob.push(note="old app")

    monkeypatch.setenv("SESSIONSYNC_LIVE_MAJOR", "11")
    alice.pull()
    assert alice.saved_as_new() == [("My Song 12.als", "My Song.als")]
    alice.push(note="make Sam's set the song")
    assert marker(alice.project_root / "My Song.als") == "Sam's 12 work"
    assert versions.set_major(alice.project_root / "My Song.als") == 11  # Alice can open it
    assert not (alice.project_root / "My Song 12.als").exists()
    assert "My Song 12.als" not in alice.read_manifest(alice.base_version).files


def test_nothing_is_merged_over_a_song_set_changed_here(song, monkeypatch):
    alice, bob = song
    as_bob(monkeypatch)
    (bob.project_root / "My Song 12.als").write_bytes(live12("new"))
    (bob.project_root / "My Song.als").write_bytes(live12("also changed"))
    assert bob.saved_as_new() == []


def test_a_push_says_what_the_songs_live_cannot_hold(song, monkeypatch):
    alice, bob = song
    as_bob(monkeypatch)
    (bob.project_root / "My Song.als").write_bytes(live12("tuned"))
    assert bob.lost_in_push() == {"My Song.als": ["a tuning system"]}
