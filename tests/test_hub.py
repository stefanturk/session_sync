import gzip

from sessionsync.core.hub import Hub, compare_scans, pull_if_safe
from sessionsync.core.repository import Repository
from tests.conftest import ALICE, BOB, als_with_tracks, write_project


def make_hub(tmp_path, name="Sam"):
    path = tmp_path / "Drive" / f"{name} Stefan Sketches"
    path.mkdir(parents=True)
    return Hub(path, name)


def test_only_songs_are_listed_and_scanning_writes_nothing(tmp_path):
    hub = make_hub(tmp_path)
    (hub.path / "Bounce.mp3").write_bytes(b"x")
    (hub.path / "Loose stems").mkdir()
    (hub.path / "Loose stems" / "kick.wav").write_bytes(b"x")
    before = sorted(p.relative_to(hub.path).as_posix() for p in hub.path.rglob("*"))

    assert hub.songs() == []
    assert sorted(p.relative_to(hub.path).as_posix() for p in hub.path.rglob("*")) == before


def test_add_song_then_other_person_joins_and_sees_it(tmp_path):
    hub = make_hub(tmp_path)
    project = write_project(tmp_path / "alice" / "Song A", "Song A")
    hub.add_song("Song A", project, ALICE, note="Start")

    songs = hub.songs()
    assert [s.name for s in songs] == ["Song A"]
    assert songs[0].latest_version == 1 and songs[0].latest_author == "Alice"

    bob = hub.join_song("Song A", tmp_path / "bob-music", BOB)
    assert bob.project_root == tmp_path / "bob-music" / "Sam" / "Song A"
    assert (bob.project_root / "Song A.als").exists()


def test_song_a_scenario_from_the_brief(tmp_path):
    """I make Song A, Sam revs it twice, I rev it; Sam adds Song B in the middle."""
    hub = make_hub(tmp_path)
    mine = hub.add_song("Song A", write_project(tmp_path / "me" / "Song A", "Song A"), ALICE)
    sam = hub.join_song("Song A", tmp_path / "sam-music", BOB)

    _, seen = compare_scans(hub, None)  # I connected the hub: v1 is the starting point
    assert seen == {"Song A": 1}

    for note, tracks in (("sax", {"Sax": "1"}), ("drums", {"Sax": "1", "Drums": "1"})):
        (sam.project_root / "Song A.als").write_bytes(als_with_tracks(tracks))
        sam.push(note=note)

    hub.add_song("Song B", write_project(tmp_path / "sam" / "Song B", "Song B"), BOB)  # mid-way

    events, seen = compare_scans(hub, seen)
    by_kind = {(e.kind, e.song): e for e in events}
    assert by_kind[("new_versions", "Song A")].versions == [2, 3]
    assert by_kind[("new_versions", "Song A")].authors == ["Bob", "Bob"]
    assert by_kind[("new_versions", "Song A")].summaries[1] == "Added Drums"
    assert ("new_song", "Song B") in by_kind
    assert seen == {"Song A": 3, "Song B": 1}

    # Nothing new on a second look.
    assert compare_scans(hub, seen)[0] == []

    # My push is refused until I pull; pulling is automatic because I have no unpushed work.
    mine_again = Repository.open(mine.project_root, author=ALICE)
    assert pull_if_safe(mine_again).to_version == 3
    (mine.project_root / "Samples" / "Recorded" / "Mine.wav").write_bytes(b"mine")
    assert mine_again.push().version == 4


def test_auto_pull_leaves_unpushed_work_alone(tmp_path):
    hub = make_hub(tmp_path)
    mine = hub.add_song("S", write_project(tmp_path / "me" / "S", "S"), ALICE)
    sam = hub.join_song("S", tmp_path / "sam-music", BOB)
    (sam.project_root / "Samples" / "Recorded" / "New.wav").write_bytes(b"n")
    sam.push()

    (mine.project_root / "Samples" / "Recorded" / "Mine.wav").write_bytes(b"m")  # unpushed work
    repo = Repository.open(mine.project_root, author=ALICE)
    assert pull_if_safe(repo) is None
    assert repo.base_version == 1


def test_auto_pull_skips_an_open_session(tmp_path):
    hub = make_hub(tmp_path)
    mine = hub.add_song("S", write_project(tmp_path / "me" / "S", "S"), ALICE)
    sam = hub.join_song("S", tmp_path / "sam-music", BOB)
    (sam.project_root / "Samples" / "Recorded" / "New.wav").write_bytes(b"n")
    sam.push()

    repo = Repository.open(mine.project_root, author=ALICE)
    repo.state.session = {"key": "k"}
    assert pull_if_safe(repo) is None


def test_a_version_still_arriving_from_drive_is_caught_next_time(tmp_path):
    hub = make_hub(tmp_path)
    hub.add_song("S", write_project(tmp_path / "me" / "S", "S"), ALICE)
    _, seen = compare_scans(hub, None)
    manifests = hub.path / "S" / "manifests"
    (manifests / "v000002.json").write_text("{ half written")  # Drive mid-sync
    events, seen2 = compare_scans(hub, seen)
    assert events == [] and seen2 == {"S": 1}


def test_join_refuses_a_folder_with_files(tmp_path):
    import pytest
    hub = make_hub(tmp_path)
    hub.add_song("S", write_project(tmp_path / "me" / "S", "S"), ALICE)
    taken = tmp_path / "m" / "Sam" / "S"
    taken.mkdir(parents=True)
    (taken / "a.txt").write_text("x")
    with pytest.raises(FileExistsError):
        hub.join_song("S", tmp_path / "m", BOB)


def test_add_song_refuses_a_name_in_use(tmp_path):
    import pytest
    hub = make_hub(tmp_path)
    p = write_project(tmp_path / "me" / "S", "S")
    hub.add_song("S", p, ALICE)
    with pytest.raises(FileExistsError):
        hub.add_song("S", p, ALICE)
