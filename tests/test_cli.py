import pytest
from typer.testing import CliRunner

from sessionsync.cli import app
from tests.conftest import write_project

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
        (synced / "My Song.als").write_bytes(b"alice edit")
        run("push", "--project", synced, "--note", "Alice")

        result = run("status", "--project", clone)

        assert "behind" in result.stdout.lower()
        assert "Pull latest" in result.stdout

    def test_lists_unpushed_work_with_its_upload_size(self, synced):
        (synced / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"g" * 2048)

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
        (synced / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"g" * 1024)

        result = run("push", "--project", synced, "--note", "Guitar")

        assert result.exit_code == 0
        assert "v2" in result.stdout
        assert "1.0 KB" in result.stdout

    def test_a_stale_push_is_refused_with_an_explanation(self, synced, remote_dir, tmp_path):
        clone = tmp_path / "bob" / "My Song"
        run("clone", remote_dir, clone)
        (synced / "My Song.als").write_bytes(b"alice edit")
        run("push", "--project", synced, "--note", "Alice")
        (clone / "Samples" / "Recorded" / "Bass.wav").write_bytes(b"bass")

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
        (synced / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")
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
        (synced / "My Song.als").write_bytes(b"alice edit")
        run("push", "--project", synced, "--note", "Alice")

        result = run("session", "start", "--project", clone)

        assert result.exit_code != 0
        assert "Pull latest before you start" in result.stdout

    def test_review_lists_what_you_recorded_this_session(self, synced):
        run("session", "start", "--project", synced)
        (synced / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")

        result = run("review", "--project", synced)

        assert "Samples/Recorded/Guitar.wav" in result.stdout
        assert "Added" in result.stdout


class TestHistory:
    def test_reads_like_a_list_of_sessions_not_a_git_log(self, synced):
        (synced / "My Song.als").write_bytes(b"v2")
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

        (synced / "My Song.als").write_bytes(b"v2")
        run("push", "--project", synced, "--note", "Late night")

        result = run("history", "--project", synced)

        expected = datetime.now().astimezone().strftime("%d %b %Y, %-I:%M")
        assert expected in result.stdout


class TestMediaWarnings:
    def test_status_warns_when_audio_lives_outside_the_project(self, synced, tmp_path):
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

        assert "Collect All and Save" in result.stdout
        assert "Borrowed Loop.wav" in result.stdout


class TestWho:
    def test_reports_when_nobody_else_is_working(self, synced):
        result = run("who", "--project", synced)

        assert "Nobody else" in result.stdout
