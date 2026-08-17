"""Pull must never quietly replace work you have not pushed."""

import pytest
from typer.testing import CliRunner

from sessionsync.cli import app
from sessionsync.core.errors import LocalChangesWouldBeReplacedError

runner = CliRunner()


@pytest.fixture
def alice_changed_the_set(alice, bob, project):
    """Bob has a working copy of v1; Alice then publishes v2."""
    (project / "My Song.als").write_bytes(b"alices arrangement")
    alice.push(note="Alice arrangement")
    return alice


class TestEngine:
    def test_refuses_to_replace_a_file_you_edited_yourself(
        self, alice_changed_the_set, bob
    ):
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        with pytest.raises(LocalChangesWouldBeReplacedError) as excinfo:
            bob.pull()

        assert excinfo.value.paths == ["My Song.als"]

    def test_a_refused_pull_changes_absolutely_nothing(self, alice_changed_the_set, bob):
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        with pytest.raises(LocalChangesWouldBeReplacedError):
            bob.pull()

        assert (bob.project_root / "My Song.als").read_bytes() == b"bobs own arrangement"
        assert bob.base_version == 1

    def test_your_own_new_recordings_never_stand_in_the_way_of_a_pull(
        self, alice_changed_the_set, bob
    ):
        (bob.project_root / "Samples" / "Recorded" / "Bass.wav").write_bytes(b"bob bass")

        result = bob.pull()

        assert result.to_version == 2
        assert (bob.project_root / "Samples" / "Recorded" / "Bass.wav").read_bytes() == b"bob bass"

    def test_a_file_you_deleted_that_the_new_version_still_has_is_just_restored(
        self, alice, bob, project
    ):
        (project / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")
        alice.push(note="Guitar")
        (bob.project_root / "Samples" / "Recorded" / "Vocal.wav").unlink()

        result = bob.pull()

        assert result.to_version == 2
        assert (bob.project_root / "Samples" / "Recorded" / "Vocal.wav").exists()

    def test_you_can_pull_anyway_once_you_have_been_told(
        self, alice_changed_the_set, bob
    ):
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        result = bob.pull(replace_my_versions=True)

        assert result.to_version == 2
        assert (bob.project_root / "My Song.als").read_bytes() == b"alices arrangement"

    def test_your_replaced_version_is_kept_where_you_can_find_it(
        self, alice_changed_the_set, bob
    ):
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        bob.pull(replace_my_versions=True)

        rescued = list(bob.state.trash_dir.rglob("My Song.als"))
        assert rescued and rescued[0].read_bytes() == b"bobs own arrangement"


class TestCommandLine:
    def _bob_cli(self, tmp_path, remote_dir, monkeypatch):
        monkeypatch.setenv("SESSIONSYNC_NAME", "Bob")
        monkeypatch.setenv("SESSIONSYNC_EMAIL", "bob@example.com")

    def test_pull_stops_and_names_the_files_at_risk(
        self, alice_changed_the_set, bob, monkeypatch
    ):
        monkeypatch.setenv("SESSIONSYNC_NAME", "Bob")
        monkeypatch.setenv("SESSIONSYNC_EMAIL", "bob@example.com")
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        result = runner.invoke(app, ["pull", "--project", str(bob.project_root)], input="n\n")

        assert "My Song.als" in result.stdout
        assert (bob.project_root / "My Song.als").read_bytes() == b"bobs own arrangement"

    def test_answering_yes_pulls_and_says_where_your_copy_went(
        self, alice_changed_the_set, bob, monkeypatch
    ):
        monkeypatch.setenv("SESSIONSYNC_NAME", "Bob")
        monkeypatch.setenv("SESSIONSYNC_EMAIL", "bob@example.com")
        (bob.project_root / "My Song.als").write_bytes(b"bobs own arrangement")

        result = runner.invoke(app, ["pull", "--project", str(bob.project_root)], input="y\n")

        assert "trash" in result.stdout.lower()
        assert (bob.project_root / "My Song.als").read_bytes() == b"alices arrangement"
