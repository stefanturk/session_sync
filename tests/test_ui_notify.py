"""Notification text goes through AppleScript, where quoting matters."""

from __future__ import annotations

from sessionsync.ui.notify import notification_script


def test_shows_the_message_under_the_project_name():
    script = notification_script("Alice started working.", title="My Song")
    assert 'display notification "Alice started working."' in script
    assert 'with title "My Song"' in script


def test_an_apostrophe_in_a_project_name_survives():
    script = notification_script("Pushed.", title="Bob's Song")
    assert "Bob's Song" in script


def test_a_quote_in_the_message_cannot_end_the_applescript_string():
    script = notification_script('Bob pushed "final mix".', title="My Song")
    assert r'\"final mix\"' in script


def test_a_backslash_is_escaped_before_the_quotes_are():
    script = notification_script(r"C:\path", title="X")
    assert r"C:\\path" in script


def test_newlines_do_not_break_the_one_line_script():
    script = notification_script("Two\nlines", title="X")
    assert "\n" not in script
