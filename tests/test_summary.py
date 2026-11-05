from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.manifest import ManifestDiff
from sessionsync.core.summary import summarise
from tests.conftest import als_with_tracks, write_project


def diff(added=(), modified=(), deleted=()):
    return ManifestDiff(list(added), list(modified), list(deleted), [], 0, 0)


def test_first_version_counts_tracks():
    assert summarise(diff(), None, {"A": "1", "B": "2"}, first=True) == "First version · 2 tracks"
    assert summarise(diff(), None, None, first=True) == "First version"


def test_added_and_changed_tracks_are_named():
    old = {"Drums": "a", "Bass": "b"}
    new = {"Drums": "a2", "Bass": "b", "Sax": "c"}
    assert summarise(diff(modified=["x.als"]), old, new) == "Added Sax · Changed Drums"


def test_removed_track_and_new_samples():
    text = summarise(
        diff(added=["Samples/Recorded/a.wav", "Samples/Recorded/b.wav"]),
        {"Drums": "a", "Old": "z"},
        {"Drums": "a"},
    )
    assert text == "Removed Old · 2 new samples"


def test_long_lists_are_shortened():
    new = {f"T{i}": "1" for i in range(6)}
    assert summarise(diff(), {}, new) == "Added T0, T1, T2 and 3 more"


def test_falls_back_to_files_without_track_data():
    assert summarise(diff(modified=["a.als"], deleted=["b"]), None, None) == "1 file changed · 1 file removed"


def test_fingerprints_follow_the_music_not_the_view(tmp_path):
    root = write_project(tmp_path / "S", "S")
    adapter = AbletonAdapter()
    (root / "S.als").write_bytes(als_with_tracks({"Drums": "one", "Bass": "x"}))
    before = adapter.track_fingerprints(root)
    # Folding a track is not a change to the music...
    raw = (root / "S.als")
    import gzip
    raw.write_bytes(gzip.compress(gzip.decompress(raw.read_bytes()).replace(b'TrackUnfolded Value="true"', b'TrackUnfolded Value="false"')))
    assert adapter.track_fingerprints(root) == before
    # ...but changing the content is.
    (root / "S.als").write_bytes(als_with_tracks({"Drums": "two", "Bass": "x"}))
    after = adapter.track_fingerprints(root)
    assert after["Drums"] != before["Drums"] and after["Bass"] == before["Bass"]


def test_unreadable_set_gives_none(tmp_path):
    root = write_project(tmp_path / "S", "S")  # fixture set has no <LiveSet>
    assert AbletonAdapter().track_fingerprints(root) is None
