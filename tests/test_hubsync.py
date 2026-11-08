from sessionsync.core.hub import Hub
from sessionsync.core.repository import Repository
from sessionsync.ui import hubsync
from sessionsync.ui.settings import Settings
from tests.conftest import ALICE, BOB, als_with_tracks, write_project, record


def setup_world(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "home"))
    settings = Settings()
    settings.set_local_root(tmp_path / "alice-music")
    folder = tmp_path / "Drive" / "Sam Stefan Sketches"
    folder.mkdir(parents=True)
    hub = settings.add_hub(folder, "Sam")
    return settings, hub


def test_rows_show_new_behind_and_current(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    bob_song = hub.add_song("Song B", write_project(tmp_path / "bob" / "Song B", "Song B"), BOB)
    hubsync.share(hub, write_project(tmp_path / "me" / "Song A", "Song A"), settings, ALICE)

    states = {r.song.name: r.state for r in hubsync.song_rows(hub, settings)}
    assert states == {"Song A": "current", "Song B": "new"}

    hubsync.join(hub, "Song B", settings, ALICE)
    record(bob_song.project_root, "Samples/Recorded/x.wav", b"x")
    bob_song.push(note="more")
    rows = {r.song.name: r for r in hubsync.song_rows(hub, settings)}
    assert rows["Song B"].state == "behind" and rows["Song B"].label == "1 newer version to get"
    assert rows["Song B"].detail.startswith("Bob")


def test_check_hubs_reports_and_pulls_closed_songs(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    hubsync.share(hub, write_project(tmp_path / "me" / "Song A", "Song A"), settings, ALICE)
    assert hubsync.check_hubs(settings, ALICE) == []  # first look is the starting point

    sam = hub.join_song("Song A", tmp_path / "sam", BOB)
    (sam.project_root / "Song A.als").write_bytes(als_with_tracks({"Drums": "1"}))
    sam.push()
    (sam.project_root / "Song A.als").write_bytes(als_with_tracks({"Drums": "1", "Sax": "1"}))
    sam.push()
    hub.add_song("Song B", write_project(tmp_path / "sam" / "Song B", "Song B"), BOB)

    news = hubsync.check_hubs(settings, ALICE)
    messages = [n.text for n in news]
    assert "Sam added a new song: Song B" in messages
    mine = [m for m in messages if "Song A" in m][0]
    assert mine.startswith("Bob pushed v2–v3 of Song A — ") and "Added Sax" in mine
    assert mine.endswith("(updated on this computer)")
    pushed = [n for n in news if n.kind == "pushed"][0]
    assert pushed.who == "Bob" and pushed.versions == [2, 3] and pushed.pulled

    me = Repository.open(settings.song_path(hub, "Song A"), author=ALICE, storage=hub.storage_for("Song A"))
    assert me.base_version == 3
    assert hubsync.check_hubs(settings, ALICE) == []


def test_open_songs_and_unpushed_work_are_not_pulled(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    mine_path = hubsync.share(hub, write_project(tmp_path / "me" / "S", "S"), settings, ALICE)
    hubsync.check_hubs(settings, ALICE)
    sam = hub.join_song("S", tmp_path / "sam", BOB)
    record(sam.project_root, "Samples/Recorded/n.wav", b"n")
    sam.push()

    msgs = hubsync.check_hubs(settings, ALICE, open_roots={mine_path})
    assert msgs and "updated on this computer" not in msgs[0].text and msgs[0].path == mine_path
    assert Repository.open(mine_path, author=ALICE, storage=hub.storage_for("S")).base_version == 1


def test_my_own_pushes_are_not_news(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    path = hubsync.share(hub, write_project(tmp_path / "me" / "S", "S"), settings, ALICE)
    hubsync.check_hubs(settings, ALICE)
    repo = Repository.open(path, author=ALICE, storage=hub.storage_for("S"))
    record(path, "Samples/Recorded/mine.wav", b"m")
    repo.push()
    assert hubsync.check_hubs(settings, ALICE) == []


def test_rows_say_who_is_working_and_who_has_unpushed_changes(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    sam = hub.add_song("Song B", write_project(tmp_path / "bob" / "Song B", "Song B"), BOB)
    assert sam.refresh_presence(live_here=True) == "working"

    row = hubsync.song_rows(hub, settings, ALICE)[0]
    assert row.working == ["Bob"] and "working on it now" in row.label
    assert hubsync.song_rows(hub, settings, BOB)[0].working == []  # not about yourself

    record(sam.project_root, "Samples/Recorded/x.wav", b"x")
    sam.refresh_presence(live_here=False)
    row = hubsync.song_rows(hub, settings, ALICE)[0]
    assert row.working == [] and row.unpushed == ["Bob"]
    assert "not pushed" in row.label


def test_live_warning_when_opening_a_set_someone_is_on(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    sam = hub.add_song("Song B", write_project(tmp_path / "bob" / "Song B", "Song B"), BOB)
    sam.refresh_presence(live_here=True)
    mine = hubsync.join(hub, "Song B", settings, ALICE)

    warning = hubsync.live_warning(settings, ALICE, mine / "Song B.als")
    assert warning is not None and "Bob is working on Song B" in warning.text
    assert hubsync.live_warning(settings, ALICE, tmp_path / "elsewhere" / "x.als") is None
    assert hubsync.live_warning(settings, ALICE, None) is None


def test_live_warning_when_opening_the_shared_copy_directly(tmp_path, monkeypatch):
    settings, hub = setup_world(tmp_path, monkeypatch)
    hub.add_song("Song B", write_project(tmp_path / "bob" / "Song B", "Song B"), BOB)
    warning = hubsync.live_warning(settings, ALICE, hub.path / "Song B" / "Song B.als")
    assert warning is not None and "shared copy" in warning.text
