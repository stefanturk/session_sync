import pytest
from typer.testing import CliRunner

from sessionsync.cli import app
from tests.conftest import write_project, record, als_marked

runner = CliRunner()


@pytest.fixture(autouse=True)
def identity(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice")
    monkeypatch.setenv("SESSIONSYNC_EMAIL", "alice@example.com")


@pytest.fixture
def remote_dir(tmp_path):
    return tmp_path / "remote" / "My Song"


@pytest.fixture
def song(tmp_path):
    return write_project(tmp_path / "alice" / "My Song")


def run(*args):
    return runner.invoke(app, [str(a) for a in args])


@pytest.fixture
def synced(song, remote_dir):
    run("init", song, "--remote", remote_dir)
    run("push", "--project", song, "--note", "Initial version")
    return song


class TestInit:
    def test_sets_up_a_project_and_says_so_plainly(self, song, remote_dir):
        result = run("init", song, "--remote", remote_dir)

        assert result.exit_code == 0
        assert "My Song" in result.stdout
        assert (song / ".sessionsync" / "state.db").exists()

    def test_refuses_a_folder_that_is_not_an_ableton_project(self, tmp_path, remote_dir):
        plain = tmp_path / "Holiday Photos"
        plain.mkdir()

        result = run("init", plain, "--remote", remote_dir)

        assert result.exit_code != 0
        assert "Ableton project" in result.stdout


class TestStatus:
    def test_shows_both_versions_and_an_up_to_date_message(self, synced):
        result = run("status", "--project", synced)

        assert result.exit_code == 0
        assert "v1" in result.stdout
        assert "Up to date" in result.stdout

    def test_warns_loudly_when_someone_else_has_pushed(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        (synced / "My Song.als").write_bytes(als_marked(b"alice edit"))
        run("push", "--project", synced, "--note", "Alice")

        result = run("status", "--project", clone)

        assert "behind" in result.stdout.lower()
        assert "Pull latest" in result.stdout

    def test_lists_unpushed_work_with_its_upload_size(self, synced):
        record(synced, "Samples/Recorded/Guitar.wav", b"g" * 2048)

        result = run("status", "--project", synced)

        assert "Samples/Recorded/Guitar.wav" in result.stdout
        assert "2.0 KB" in result.stdout

    def test_says_who_else_is_working_right_now(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        run("session", "start", "--project", clone)

        result = run("status", "--project", synced)

        assert "Alice has been working" in result.stdout


class TestPush:
    def test_reports_the_new_version_and_what_was_uploaded(self, synced):
        record(synced, "Samples/Recorded/Guitar.wav", b"g" * 1024)

        result = run("push", "--project", synced, "--note", "Guitar")

        assert result.exit_code == 0
        assert "v2" in result.stdout
        assert "Uploaded: 1." in result.stdout  # the 1 KB take plus the Set that now uses it

    def test_a_stale_push_is_refused_with_an_explanation(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        (synced / "My Song.als").write_bytes(als_marked(b"alice edit"))
        run("push", "--project", synced, "--note", "Alice")
        record(clone, "Samples/Recorded/Bass.wav", b"bass")

        result = run("push", "--project", clone, "--note", "Bob bass")

        assert result.exit_code != 0
        assert "Pull latest" in result.stdout

    def test_pushing_nothing_is_a_gentle_message_not_a_crash(self, synced):
        result = run("push", "--project", synced, "--note", "nothing")

        assert result.exit_code == 0
        assert "Nothing has changed" in result.stdout


class TestPullAndClone:
    def test_clone_reconstructs_the_project(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"

        result = run("clone", remote_dir, clone)

        assert result.exit_code == 0
        assert (clone / "My Song.als").exists()
        assert (clone / "Samples" / "Recorded" / "Vocal.wav").read_bytes() == b"vocal audio"

    def test_pull_reports_what_arrived(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        record(synced, "Samples/Recorded/Guitar.wav", b"guitar")
        run("push", "--project", synced, "--note", "Guitar")

        result = run("pull", "--project", clone)

        assert "v2" in result.stdout
        assert "Samples/Recorded/Guitar.wav" in result.stdout


class TestSession:
    def test_starting_a_session_confirms_you_are_current(self, synced):
        result = run("session", "start", "--project", synced)

        assert result.exit_code == 0
        assert "v1" in result.stdout

    def test_starting_a_session_while_behind_is_stopped_before_any_work(
        self, synced, remote_dir, tmp_path
    ):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        (synced / "My Song.als").write_bytes(als_marked(b"alice edit"))
        run("push", "--project", synced, "--note", "Alice")

        result = run("session", "start", "--project", clone)

        assert result.exit_code != 0
        assert "Pull latest before you start" in result.stdout

    def test_review_lists_what_you_recorded_this_session(self, synced):
        run("session", "start", "--project", synced)
        record(synced, "Samples/Recorded/Guitar.wav", b"guitar")

        result = run("review", "--project", synced)

        assert "Samples/Recorded/Guitar.wav" in result.stdout
        assert "Added" in result.stdout


class TestHistory:
    def test_reads_like_a_list_of_sessions_not_a_git_log(self, synced):
        (synced / "My Song.als").write_bytes(als_marked(b"v2"))
        run("push", "--project", synced, "--note", "Guitar overdubs")

        result = run("history", "--project", synced)

        assert "v2" in result.stdout
        assert "Alice" in result.stdout
        assert "Guitar overdubs" in result.stdout
        for jargon in ("commit", "blob", "HEAD", "branch"):
            assert jargon not in result.stdout


class TestTimes:
    def test_history_shows_times_in_your_own_timezone(self, synced):
        from datetime import datetime

        (synced / "My Song.als").write_bytes(als_marked(b"v2"))
        run("push", "--project", synced, "--note", "Late night")

        result = run("history", "--project", synced)

        expected = datetime.now().astimezone().strftime("%d %b %Y, %-I:%M")
        assert expected in result.stdout


class TestMediaWarnings:
    def test_status_says_outside_audio_will_be_copied_in(self, synced, tmp_path):
        import gzip

        stray = tmp_path / "Downloads" / "Borrowed Loop.wav"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(b"loop")
        xml = (
            '<?xml version="1.0"?><Ableton MinorVersion="11.0_11300"><LiveSet>'
            "<SampleRef><FileRef>"
            '<RelativePath Value="Samples/Imported/Borrowed Loop.wav" />'
            f'<Path Value="{stray}" />'
            "</FileRef></SampleRef></LiveSet></Ableton>"
        )
        (synced / "My Song.als").write_bytes(gzip.compress(xml.encode()))

        result = run("status", "--project", synced)

        assert "copied into Samples/Imported on Push" in result.stdout
        assert "Borrowed Loop.wav" in result.stdout

    def test_push_dry_run_shows_what_is_left_behind_and_changes_nothing(self, synced):
        (synced / "Samples" / "Recorded" / "Old Take.wav").write_bytes(b"x" * 3000)

        result = run("push", "--project", synced, "--dry-run")

        assert result.exit_code == 0, result.output
        assert "Not used by any Live Set (1" in result.stdout
        assert "Old Take.wav" in result.stdout
        assert "Pushed" not in result.stdout

    def test_open_rev_gets_an_old_revision_ready(self, synced):
        result = run("open-rev", "1", "--project", synced, "--no-open")

        assert result.exit_code == 0, result.output
        assert "Rev 1 is ready" in result.stdout
        assert "Past Revisions" in result.stdout


class TestWho:
    def test_reports_when_nobody_else_is_working(self, synced):
        result = run("who", "--project", synced)

        assert "Nobody else" in result.stdout


class TestSharedFolders:
    @pytest.fixture
    def hub_dir(self, tmp_path):
        folder = tmp_path / "Drive" / "Sam Stefan Sketches"
        folder.mkdir(parents=True)
        (folder / "loose bounce.mp3").write_bytes(b"x")
        return folder

    def test_connect_share_and_see_news(self, hub_dir, song, tmp_path, monkeypatch):
        assert "0 song" in run("hub", "add", hub_dir, "--name", "Sam").output
        assert run("hub", "news").output.strip() == "Nothing new."  # first look = starting point

        assert "now shared in Sam" in run("song", "new", "Sam", song).output
        news = run("hub", "news").output
        assert "new song 'My Song'" in news

        listing = run("hub", "list").output
        assert "Sam" in listing and "My Song  v1" in listing
        assert "loose bounce" not in listing

    def test_join_downloads_under_the_local_root(self, hub_dir, song, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))  # never touch the real one
        run("hub", "add", hub_dir, "--name", "Sam")
        run("song", "new", "Sam", song)
        result = run("song", "join", "Sam", "My Song")
        assert result.exit_code == 0
        expected = tmp_path / "home" / "Music" / "Ableton" / "SessionSync" / "Sam" / "My Song"
        assert (expected / "My Song.als").exists()

    def test_restore_builds_a_separate_copy(self, synced, tmp_path):
        result = run("restore", 1, "--to", tmp_path / "copy", "--project", synced)
        assert result.exit_code == 0
        assert (tmp_path / "copy" / "My Song.als").exists()
        again = run("restore", 1, "--to", tmp_path / "copy", "--project", synced)
        assert again.exit_code == 1 and "already exists" in again.output

    def test_unknown_hub_is_explained(self):
        result = run("song", "join", "Nobody", "x")
        assert result.exit_code == 1 and "No shared folder called 'Nobody'" in result.output


def test_song_get_to_a_chosen_folder_and_pin(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sessionsync.cli import app
    from tests.conftest import write_project

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONSYNC_HOME", str(tmp_path / "ssh"))
    monkeypatch.setenv("SESSIONSYNC_NAME", "Alice")
    monkeypatch.setenv("SESSIONSYNC_EMAIL", "alice@example.com")
    runner = CliRunner()
    hub = tmp_path / "Drive" / "Sam"
    hub.mkdir(parents=True)
    write_project(hub / "Plain", "Plain")  # a plain project already in the shared folder

    assert runner.invoke(app, ["hub", "add", str(hub)]).exit_code == 0
    got = runner.invoke(app, ["song", "get", "Sam", "Plain", "--to", str(tmp_path / "anywhere" / "Plain")])
    assert got.exit_code == 0, got.output
    assert (tmp_path / "anywhere" / "Plain" / "Plain.als").exists()

    pinned = runner.invoke(app, ["pin", "1", "-p", str(tmp_path / "anywhere" / "Plain")])
    assert pinned.exit_code == 0, pinned.output
