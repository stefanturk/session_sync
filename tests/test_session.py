from datetime import datetime, timedelta, timezone

import pytest

from sessionsync.core.errors import ProjectBehindError
from sessionsync.core.session import (
    ACTIVE_AFTER_SECONDS,
    active_sessions,
    describe_sessions,
)


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class TestStartingASession:
    def test_announces_to_everyone_else_that_you_are_working(self, alice, remote):
        alice.start_session()

        assert [s["display_name"] for s in remote.list_sessions()] == ["Alice"]

    def test_records_the_version_you_started_from(self, alice, remote):
        alice.start_session()

        assert remote.list_sessions()[0]["base_version"] == 1

    def test_takes_a_baseline_so_changes_can_be_reviewed_later(self, alice, project):
        alice.start_session()
        (project / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")

        assert alice.review().added == ["Samples/Recorded/Guitar.wav"]

    def test_refuses_to_start_when_someone_else_has_already_pushed(
        self, alice, bob, project
    ):
        (project / "My Song.als").write_bytes(b"alice edit")
        alice.push(note="Alice")

        with pytest.raises(ProjectBehindError) as excinfo:
            bob.start_session()

        assert "Pull latest" in str(excinfo.value)

    def test_can_be_forced_when_working_offline(self, alice, bob, project):
        (project / "My Song.als").write_bytes(b"alice edit")
        alice.push(note="Alice")

        bob.start_session(force=True)

        assert bob.state.session["base_version"] == 1


class TestSeeingOtherPeople:
    def test_you_can_see_who_is_working_right_now(self, alice, bob):
        bob.start_session()

        assert [s["display_name"] for s in alice.status().active_sessions] == ["Bob"]

    def test_you_are_not_shown_your_own_session(self, alice):
        alice.start_session()

        assert alice.status().active_sessions == []

    def test_a_session_that_stopped_beating_is_no_longer_shown_as_active(self, remote):
        remote.write_session("ghost-1", {"display_name": "Ghost", "last_heartbeat": ago(3600)})

        assert active_sessions(remote) == []

    def test_a_recent_heartbeat_counts_as_active(self, remote):
        remote.write_session(
            "bob-1", {"display_name": "Bob", "last_heartbeat": ago(ACTIVE_AFTER_SECONDS - 30)}
        )

        assert len(active_sessions(remote)) == 1

    def test_heartbeats_are_refreshed_while_you_work(self, alice, remote):
        alice.start_session()
        first = remote.list_sessions()[0]["last_heartbeat"]

        alice.heartbeat()

        assert remote.list_sessions()[0]["last_heartbeat"] >= first

    def test_pushing_clears_your_session_so_nobody_waits_on_you(
        self, alice, project, remote
    ):
        alice.start_session()
        (project / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")

        alice.push(note="Guitar")

        assert remote.list_sessions() == []

    def test_ending_a_session_without_pushing_also_clears_it(self, alice, remote):
        alice.start_session()

        alice.end_session()

        assert remote.list_sessions() == []


class TestPresenceIsAdvisoryOnly:
    def test_someone_elses_open_session_does_not_block_your_push(
        self, alice, bob, project
    ):
        bob.start_session()
        (project / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")

        assert alice.push(note="Guitar").version == 2

    def test_a_crashed_clients_stale_session_never_blocks_anyone(
        self, alice, project, remote
    ):
        remote.write_session("crashed-1", {"display_name": "Ghost", "last_heartbeat": ago(99999)})
        (project / "Samples" / "Recorded" / "Guitar.wav").write_bytes(b"guitar")

        assert alice.push(note="Guitar").version == 2


class TestWording:
    def test_says_who_is_working_and_since_when(self):
        text = describe_sessions(
            [{"display_name": "Alice", "started_at": ago(600), "state": "working"}]
        )

        assert "Alice" in text
        assert "working" in text.lower()

    def test_calls_out_someone_who_is_pushing_right_now(self):
        text = describe_sessions(
            [{"display_name": "Alice", "started_at": ago(60), "state": "pushing"}]
        )

        assert "pushing" in text.lower()

    def test_says_nothing_when_nobody_is_working(self):
        assert describe_sessions([]) == ""
