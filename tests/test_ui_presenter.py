"""What the window shows, decided without Qt in the way."""

from __future__ import annotations

import pytest

from sessionsync.core.manifest import FileEntry, diff_file_maps
from sessionsync.core.repository import Status
from sessionsync.ui.presenter import notifications, screen


def entry(path: str, sha: str, size: int = 100) -> FileEntry:
    return FileEntry(path=path, sha256=sha, size=size)


def make_status(
    state: str,
    *,
    local: int = 3,
    remote: int = 3,
    added: dict | None = None,
    sessions: list | None = None,
) -> Status:
    before: dict[str, FileEntry] = {}
    after = {p: entry(p, s) for p, s in (added or {}).items()}
    return Status(
        state=state,
        local_version=local,
        remote_version=remote,
        diff=diff_file_maps(before, after),
        project_bytes=1000,
        upload_bytes=100,
        already_stored_bytes=900,
        active_sessions=sessions or [],
    )


# --- the banner ----------------------------------------------------------


def test_up_to_date_says_so_calmly():
    model = screen(make_status("up_to_date"), project_name="My Song")
    assert model.banner.level == "ok"
    assert "Up to date" in model.banner.text


def test_being_behind_is_the_loudest_thing_on_screen():
    model = screen(make_status("behind", local=23, remote=25), project_name="My Song")
    assert model.banner.level == "warning"
    assert "2 versions behind" in model.banner.text
    assert "Pull latest" in model.banner.text


def test_one_version_behind_is_not_pluralised():
    model = screen(make_status("behind", local=24, remote=25), project_name="My Song")
    assert "1 version behind" in model.banner.text


def test_unpushed_work_is_reported_as_information_not_alarm():
    status = make_status("local_changes", added={"Samples/Sax.aif": "a"})
    model = screen(status, project_name="My Song")
    assert model.banner.level == "info"
    assert "not been pushed" in model.banner.text


def test_behind_with_unpushed_work_explains_the_work_is_safe():
    status = make_status("push_blocked", local=23, remote=25, added={"Sax.aif": "a"})
    model = screen(status, project_name="My Song")
    assert model.banner.level == "danger"
    assert "Pull latest first" in model.banner.text
    assert "kept" in model.banner.text


def test_version_line_shows_both_versions():
    model = screen(make_status("behind", local=23, remote=25), project_name="My Song")
    assert model.version_line == "Latest version: v25      You have: v23"


# --- the header status word ----------------------------------------------


def test_the_header_states_where_you_stand_in_two_words():
    assert screen(make_status("up_to_date"), project_name="X").state_label == "UP TO DATE"


def test_the_header_counts_the_versions_you_are_missing():
    model = screen(make_status("behind", local=23, remote=25), project_name="X")
    assert model.state_label == "2 VERSIONS BEHIND"


def test_the_header_says_pull_required_when_behind_blocks_a_push():
    status = make_status("push_blocked", local=24, remote=25, added={"a": "a"})
    assert screen(status, project_name="X").state_label == "PULL REQUIRED"


def test_the_header_names_unpushed_work():
    status = make_status("local_changes", added={"a": "a"})
    assert screen(status, project_name="X").state_label == "UNPUSHED WORK"


def test_an_open_session_is_stated_in_the_header():
    model = screen(make_status("up_to_date"), project_name="X", session_open=True)
    assert model.state_label == "SESSION OPEN"


def test_an_open_session_never_hides_that_you_are_behind():
    model = screen(
        make_status("behind", local=1, remote=2), project_name="X", session_open=True
    )
    assert model.state_label == "1 VERSION BEHIND"


# --- the buttons ---------------------------------------------------------


def test_nothing_to_pull_when_up_to_date():
    model = screen(make_status("up_to_date"), project_name="My Song")
    assert not model.pull.enabled
    assert "nothing new" in model.pull.reason.lower()


def test_pull_is_offered_when_behind():
    model = screen(make_status("behind", local=1, remote=2), project_name="My Song")
    assert model.pull.enabled


def test_you_cannot_start_a_session_from_an_old_version():
    model = screen(make_status("behind", local=1, remote=2), project_name="My Song")
    assert not model.start.enabled
    assert "Pull latest" in model.start.reason


def test_start_session_is_offered_when_current():
    model = screen(make_status("up_to_date"), project_name="My Song")
    assert model.start.enabled
    assert model.start.label == "Start Session"


def test_an_open_session_offers_to_end_it():
    model = screen(make_status("up_to_date"), project_name="My Song", session_open=True)
    assert model.start.label == "End Session"
    assert model.start.enabled


def test_push_needs_something_to_push():
    model = screen(make_status("up_to_date"), project_name="My Song")
    assert not model.push.enabled
    assert "nothing has changed" in model.push.reason.lower()


def test_push_is_offered_when_there_is_work():
    status = make_status("local_changes", added={"Sax.aif": "a"})
    model = screen(status, project_name="My Song")
    assert model.push.enabled


def test_push_is_refused_while_behind_even_with_work():
    status = make_status("push_blocked", local=1, remote=2, added={"Sax.aif": "a"})
    model = screen(status, project_name="My Song")
    assert not model.push.enabled
    assert "Pull latest" in model.push.reason


def test_review_follows_having_changes():
    assert not screen(make_status("up_to_date"), project_name="X").review.enabled
    with_work = make_status("local_changes", added={"Sax.aif": "a"})
    assert screen(with_work, project_name="X").review.enabled


@pytest.mark.parametrize("state", ["up_to_date", "behind", "local_changes", "push_blocked"])
def test_buttons_are_never_hidden(state):
    model = screen(make_status(state, local=1, remote=2), project_name="X")
    for button in (model.pull, model.start, model.review, model.push):
        assert button.label
        if not button.enabled:
            assert button.reason, f"{button.label} is disabled without saying why"


# --- the one thing to do next --------------------------------------------


def test_pulling_is_the_thing_to_do_when_behind():
    assert screen(make_status("behind", local=1, remote=2), project_name="X").primary == "pull"


def test_pulling_comes_first_even_when_you_also_have_work():
    status = make_status("push_blocked", local=1, remote=2, added={"Sax.aif": "a"})
    assert screen(status, project_name="X").primary == "pull"


def test_pushing_is_the_thing_to_do_when_you_have_unpushed_work():
    status = make_status("local_changes", added={"Sax.aif": "a"})
    assert screen(status, project_name="X").primary == "push"


def test_starting_a_session_is_the_thing_to_do_when_all_is_current():
    assert screen(make_status("up_to_date"), project_name="X").primary == "start"


def test_nothing_is_urged_while_a_session_is_already_open_and_untouched():
    model = screen(make_status("up_to_date"), project_name="X", session_open=True)
    assert model.primary == ""


def test_the_primary_action_is_always_one_that_can_be_clicked():
    for state, local, remote in [
        ("up_to_date", 3, 3),
        ("behind", 1, 2),
        ("local_changes", 3, 3),
        ("push_blocked", 1, 2),
    ]:
        model = screen(
            make_status(state, local=local, remote=remote, added={"a.wav": "a"}),
            project_name="X",
        )
        if model.primary:
            assert getattr(model, model.primary).enabled


# --- presence ------------------------------------------------------------


def test_presence_is_shown_when_someone_else_is_working():
    session = {"display_name": "Alice", "state": "working", "started_at": "2026-08-16T20:02:00Z"}
    model = screen(make_status("up_to_date", sessions=[session]), project_name="X")
    assert "Alice" in model.presence


def test_no_presence_line_when_you_are_alone():
    assert screen(make_status("up_to_date"), project_name="X").presence == ""


def test_someone_pushing_right_now_is_a_reason_to_wait():
    session = {"display_name": "Bob", "state": "pushing", "started_at": "2026-08-16T20:02:00Z"}
    model = screen(make_status("local_changes", added={"a": "a"}, sessions=[session]), project_name="X")
    assert "pushing right now" in model.presence


# --- the change list -----------------------------------------------------


def test_changes_are_listed_with_sizes():
    status = make_status("local_changes", added={"Samples/Sax.aif": "a"})
    model = screen(status, project_name="X")
    assert model.changes == [("Added", "Samples/Sax.aif", "100 B")]


def test_change_summary_states_what_will_be_uploaded():
    status = make_status("local_changes", added={"Sax.aif": "a"})
    model = screen(status, project_name="X")
    assert "To upload: 100 B" in model.summary


def test_last_push_is_remembered_between_launches():
    last = {"version": 12, "at": "2026-08-16T21:14:00+00:00", "note": "Guitar overdubs",
            "added": 3, "modified": 1, "deleted": 0, "uploaded_bytes": 512}
    model = screen(make_status("up_to_date"), project_name="X", last_push=last)
    assert "v12" in model.last_push
    assert "Guitar overdubs" in model.last_push


def test_no_last_push_line_before_the_first_push():
    assert screen(make_status("up_to_date"), project_name="X").last_push == ""


# --- notifications -------------------------------------------------------


def test_nothing_to_notify_on_the_first_refresh():
    assert notifications(None, make_status("up_to_date")) == []


def test_nothing_to_notify_when_nothing_changed():
    before = make_status("up_to_date")
    assert notifications(before, make_status("up_to_date")) == []


def test_a_new_version_appearing_is_worth_interrupting_for():
    before = make_status("up_to_date", local=3, remote=3)
    after = make_status("behind", local=3, remote=4)
    assert notifications(before, after) == ["A new version (v4) was pushed. Pull latest."]


def test_someone_starting_work_is_announced():
    alice = {"key": "alice-1", "display_name": "Alice", "state": "working"}
    after = make_status("up_to_date", sessions=[alice])
    assert notifications(make_status("up_to_date"), after) == [
        "Alice started working on this project."
    ]


def test_someone_finishing_is_announced():
    alice = {"key": "alice-1", "display_name": "Alice", "state": "working"}
    before = make_status("up_to_date", sessions=[alice])
    assert notifications(before, make_status("up_to_date")) == ["Alice finished working."]


def test_a_heartbeat_from_the_same_person_is_not_news():
    first = {"key": "alice-1", "display_name": "Alice", "state": "working"}
    again = {"key": "alice-1", "display_name": "Alice", "state": "working"}
    before = make_status("up_to_date", sessions=[first])
    assert notifications(before, make_status("up_to_date", sessions=[again])) == []


def test_someone_starting_to_push_is_worth_knowing_before_you_do():
    working = {"key": "alice-1", "display_name": "Alice", "state": "working"}
    pushing = {"key": "alice-1", "display_name": "Alice", "state": "pushing"}
    before = make_status("up_to_date", sessions=[working])
    after = make_status("up_to_date", sessions=[pushing])
    assert notifications(before, after) == ["Alice is pushing right now."]
