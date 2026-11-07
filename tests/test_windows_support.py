"""The parts that differ on Windows: Live's log and process, link handling,
handing a link to the copy already running, and registering the link type.

Most of this runs on any platform; the registry test only runs on Windows
(the GitHub Actions Windows job runs it).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from sessionsync.core import livewatch, osinfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- Live --------------------------------------------------------------


def test_live_is_seen_in_the_windows_task_list():
    listing = (
        '"explorer.exe","4321","Console","1","120,000 K"\r\n'
        '"Ableton Live 12 Suite.exe","9876","Console","1","1,200,000 K"\r\n'
    )
    assert livewatch.running_in_tasklist(listing)
    assert not livewatch.running_in_tasklist('"explorer.exe","1","Console","1","1 K"\r\n')
    # Live's crash reporter and the like are not Live
    assert not livewatch.running_in_tasklist('"Ableton Index.exe","5","Console","1","1 K"\r\n')


def test_live_log_is_found_where_windows_keeps_it(tmp_path):
    folder = tmp_path / "Live 12.1.5" / "Preferences"
    folder.mkdir(parents=True)
    log = folder / "Log.txt"
    log.write_text(
        '2026-11-06T17:45:01.123456: info: Loading document "C:\\Users\\sam\\Desktop\\Song\\Song.als"\r\n',
        encoding="utf-8",
    )
    assert livewatch.newest_log(tmp_path) == log
    when, document = livewatch.last_document(log)
    assert str(document).endswith("Song.als")


def test_times_have_no_leading_zero_on_any_platform():
    assert osinfo.strftime(datetime(2026, 11, 6, 9, 5), "%-I:%M") == "9:05"
    assert osinfo.strftime(datetime(2026, 11, 6, 9, 5), "%-d %b") == "6 Nov"


# --- one running copy ----------------------------------------------------


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _spin(predicate, timeout_ms=4000):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    for _ in range(timeout_ms // 20):
        QApplication.processEvents()
        if predicate():
            return True
        QThread.msleep(20)
    return False


def test_a_second_copy_hands_its_link_to_the_first(qt_app):
    from sessionsync.ui import single

    name = single.server_name(f"test-{os.getpid()}")
    listener = single.Listener(name)
    assert listener.listen()
    got = []
    listener.received.connect(got.append)
    link = "sessionsync://open?repo=abc&song=My%20Song"
    # The second copy is a separate program, as it is when Windows starts one.
    second = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
         "from sessionsync.ui import single; sys.exit(0 if single.forward(sys.argv[1], name=sys.argv[2]) else 3)",
         link, name],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        assert _spin(lambda: got, timeout_ms=15000)
        assert second.wait(15) == 0
    finally:
        second.kill() if second.poll() is None else None
        listener.close()
    assert got == [link]


def test_windows_adds_a_slash_to_the_link_and_it_still_reads():
    from sessionsync.core import launcher

    assert launcher.parse_link("sessionsync://open/?repo=abc&song=My%20Song") == ("abc", "My Song")


def test_with_no_copy_running_the_first_one_carries_on(qt_app):
    from sessionsync.ui import single

    assert single.forward("show", name=single.server_name(f"nobody-{os.getpid()}"), timeout_ms=200) is False


def test_each_person_gets_their_own_copy():
    from sessionsync.ui import single

    assert single.server_name("sam") != single.server_name("stefan")
    assert single.server_name("sam") == single.server_name("sam")


# --- the link type -------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
def test_links_are_registered_for_this_user(tmp_path):
    from sessionsync.ui import winscheme

    scheme = f"sessionsync-test-{os.getpid()}"
    exe = tmp_path / "SessionSync" / "SessionSync.exe"
    try:
        assert winscheme.register(exe, scheme=scheme) is True
        assert winscheme.registered_command(scheme=scheme) == f'"{exe}" "%1"'
        assert winscheme.register(exe, scheme=scheme) is False, "unchanged: nothing written"
    finally:
        winscheme.unregister(scheme=scheme)
    assert winscheme.registered_command(scheme=scheme) is None


def test_a_development_run_never_takes_over_the_links(monkeypatch):
    from sessionsync.ui import winscheme

    called = []
    monkeypatch.setattr(winscheme, "register", lambda *a, **k: called.append(a))
    monkeypatch.delattr(sys, "frozen", raising=False)
    winscheme.register_this_app()
    assert called == []
