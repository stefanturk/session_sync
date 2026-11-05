from pathlib import Path

from sessionsync.core import livewatch

REAL = (
    '2026-08-23T14:16:58.131833: info: Loading document '
    '"/Users/Stefan/Desktop/Litty deBungus/Abelton Recordings/Better Days-1 Project/Better Days.als"'
)


def test_parses_a_real_log_line():
    when, path = livewatch.parse_loading(REAL)
    assert when.year == 2026 and when.hour == 14
    assert path.name == "Better Days.als"


def test_ignores_other_lines():
    assert livewatch.parse_loading("2026-08-23T14:16:58.1: info: Default App: Loading document to restore from...") is None
    assert livewatch.parse_loading("junk") is None


def make_log(tmp_path, *lines):
    log = tmp_path / "Live 11.3.13" / "Log.txt"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(lines) + "\n")
    return log


def test_last_loaded_document_wins(tmp_path):
    log = make_log(tmp_path, REAL, REAL.replace("Better Days", "Other").replace("T14", "T15"), "noise")
    assert livewatch.last_document(log)[1].name == "Other.als"


def test_open_only_when_live_is_running(tmp_path):
    log = make_log(tmp_path, REAL)
    assert livewatch.open_document(log=log, running=False) is None
    assert livewatch.open_document(log=log, running=True).name == "Better Days.als"


def test_is_open_in_folder(tmp_path):
    song = tmp_path / "Song Project"
    song.mkdir()
    assert livewatch.is_open_in(song, song / "Song.als")
    assert not livewatch.is_open_in(song, tmp_path / "Other Project" / "Other.als")
    assert not livewatch.is_open_in(song, None)


def test_newest_log_is_chosen(tmp_path):
    old = make_log(tmp_path, REAL)
    new = tmp_path / "Live 12.0" / "Log.txt"
    new.parent.mkdir()
    new.write_text(REAL + "\n")
    import os
    os.utime(old, (1, 1))
    assert livewatch.newest_log(tmp_path) == new
