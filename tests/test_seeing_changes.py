"""Every saved change counts, including the ones that are not on a track."""

import gzip

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.core.summary import summarise
from sessionsync.core.manifest import diff_file_maps

from tests.conftest import record


def live_set(tempo_automation: str, *, track: str = "x", locators: str = "") -> bytes:
    body = (
        f'<Tracks><MidiTrack Id="1"><Name><EffectiveName Value="Drums"/></Name>'
        f'<Notes Value="{track}"/></MidiTrack></Tracks>'
        f'<MasterTrack><AutomationEnvelopes><Envelope Value="{tempo_automation}"/>'
        f"</AutomationEnvelopes><ViewData Value='scrolled'/></MasterTrack>"
        f"<Locators><Locator Value='{locators}'/></Locators>"
        f"<Transport><CurrentTime Value='{len(track)}'/></Transport>"
    )
    return gzip.compress(
        f"<Ableton MinorVersion='11.0_11300'><LiveSet>{body}</LiveSet></Ableton>".encode(), mtime=0
    )


def fingerprints(tmp_path, data):
    project = tmp_path / "Song Project"
    (project / "Ableton Project Info").mkdir(parents=True, exist_ok=True)
    (project / "Song.als").write_bytes(data)
    return AbletonAdapter().track_fingerprints(project)


def test_a_tempo_automation_change_is_named(tmp_path):
    before = fingerprints(tmp_path, live_set("120"))
    after = fingerprints(tmp_path, live_set("128"))
    assert before["Drums"] == after["Drums"]
    assert summarise(diff_file_maps({}, {}), before, after) == "Changed Main"


def test_moving_a_locator_is_a_song_change(tmp_path):
    before = fingerprints(tmp_path, live_set("120", locators="a"))
    after = fingerprints(tmp_path, live_set("120", locators="b"))
    assert summarise(diff_file_maps({}, {}), before, after) == "Changed Song"


def test_scrolling_the_main_track_is_not_a_change(tmp_path):
    a = live_set("120")
    b = a.replace(b"x", b"x")  # same
    assert fingerprints(tmp_path, a) == fingerprints(tmp_path, b)


def test_a_version_from_before_main_was_recorded_does_not_say_added_main(tmp_path):
    after = fingerprints(tmp_path, live_set("120"))
    old = {"Drums": after["Drums"]}
    assert summarise(diff_file_maps({}, {}), old, after) == "No changes"


def test_work_done_before_the_app_was_looking_still_counts(alice):
    """Review compares with the version you are on, not with when the app noticed."""
    alice.start_session()
    record(alice.project_root, "Samples/Recorded/Late.wav", b"take")
    alice.start_session()  # pressing start (CLI) again must not hide it
    assert "Samples/Recorded/Late.wav" in alice.review().added
