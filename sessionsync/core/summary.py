"""Say what a version changed, in a few plain words.

"Added Sax · Changed Drums · 3 new samples" beats "modified 2 files". The track
names come from fingerprints the Ableton adapter takes at push time; when they
are missing (an older version, an unreadable set) the wording falls back to
files, which is always available.
"""

from __future__ import annotations

from sessionsync.core.manifest import Manifest, ManifestDiff, diff_manifests

MEDIA = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".ogg")
_SHOW = 3


def _names(names: list[str]) -> str:
    names = sorted(names)
    if len(names) <= _SHOW:
        return ", ".join(names)
    return ", ".join(names[:_SHOW]) + f" and {len(names) - _SHOW} more"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def summarise(
    diff: ManifestDiff,
    old_tracks: dict[str, str] | None,
    new_tracks: dict[str, str] | None,
    *,
    first: bool = False,
) -> str:
    """One short line. `first` marks the opening version of a project."""
    if first:
        if new_tracks:
            return f"First version · {_count(len(new_tracks), 'track')}"
        return "First version"

    parts: list[str] = []
    if old_tracks is not None and new_tracks is not None:
        added = [t for t in new_tracks if t not in old_tracks]
        removed = [t for t in old_tracks if t not in new_tracks]
        changed = [t for t in new_tracks if t in old_tracks and new_tracks[t] != old_tracks[t]]
        if added:
            parts.append(f"Added {_names(added)}")
        if removed:
            parts.append(f"Removed {_names(removed)}")
        if changed:
            parts.append(f"Changed {_names(changed)}")

    samples = [p for p in diff.added if p.lower().endswith(MEDIA)]
    if samples:
        parts.append(_count(len(samples), "new sample"))

    if not parts:
        # Nothing track-level to say (or no track data): describe the files.
        bits = []
        if diff.added:
            bits.append(f"{_count(len(diff.added), 'file')} added")
        if diff.modified:
            bits.append(f"{_count(len(diff.modified), 'file')} changed")
        if diff.deleted:
            bits.append(f"{_count(len(diff.deleted), 'file')} removed")
        return " · ".join(bits) or "No changes"
    return " · ".join(parts)


def describe_version(previous: Manifest | None, current: Manifest) -> str:
    """The stored summary if the pusher's app wrote one, else a file-level one."""
    if current.summary:
        return current.summary
    if previous is None:
        return summarise(diff_manifests(Manifest(0, current.repository_id), current), None, None, first=True)
    return summarise(diff_manifests(previous, current), None, None)
