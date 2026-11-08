"""Ableton Live project awareness.

An Ableton project is a folder holding one or more .als Live Sets plus an
"Ableton Project Info" folder and, once Collect All and Save has run, the audio
it uses under Samples/. We track what is needed to reopen the project on another
Mac, and skip what Live or macOS regenerates by itself.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from sessionsync.adapters.base import ProjectInfo, ValidationResult

PROJECT_INFO_DIR = "Ableton Project Info"

# Directories Live or the OS owns; their contents are never part of a version.
EXCLUDED_DIRS = {
    "Backup",  # Live's own auto-backups — repository history replaces these
    "Past Revisions",  # one Live Set per pushed revision, written by Push itself
    ".sessionsync",
    ".git",
    "__MACOSX",
    ".Trash",
}

EXCLUDED_SUFFIXES = {
    ".asd",  # Live regenerates analysis files on demand
    ".tmp",
    ".lock",
}

EXCLUDED_NAMES = {".DS_Store", "Icon\r", "Thumbs.db", "desktop.ini"}

# Parts of a track that change when you merely look around (folding, selection),
# not when you change the music. Dropped before fingerprinting a track.
_VIEW_NOISE = {
    "TrackUnfolded", "ViewData", "LastSelectedTimeableIndex",
    "LastSelectedClipEnvelopeIndex", "SelectedEnvelope", "IsContentSelectedInDocument",
    "ViewStates",
}
_TRACK_TAGS = {"AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"}
#: Song-wide parts worth noticing. Not the transport: it records where the play
#: head was, which changes every time someone just listens.
_SONG_TAGS = ("Locators", "Scenes", "GroovePool")
#: Names the summary uses for the parts of a set that are not ordinary tracks.
MAIN_TRACK = "Main"
SONG_PARTS = "Song"


def _digest(elements) -> str:
    """A short hash of some XML, ignoring what Live rewrites on every save."""
    digest = hashlib.sha1()
    for element in elements:
        for noisy in list(element.iter()):
            noisy.attrib.pop("Id", None)  # Live renumbers these on every save
            for child in list(noisy):
                if child.tag in _VIEW_NOISE:
                    noisy.remove(child)
        digest.update(ET.tostring(element))
    return digest.hexdigest()[:12]

_MINOR_VERSION_RE = re.compile(rb"MinorVersion=\"([^\"]+)\"|MinorVersion='([^']+)'")
_FILE_REF_RE = re.compile(rb"<FileRef>.*?</FileRef>", re.DOTALL)


def _attr(chunk: bytes, tag: bytes) -> str:
    """Read <Tag Value="..." /> out of a Live Set fragment."""
    match = re.search(rb"<" + tag + rb'\s+Value="([^"]*)"', chunk)
    if not match:
        return ""
    return _unescape(match.group(1).decode("utf-8", "replace"))


def _unescape(value: str) -> str:
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        value = value.replace(entity, char)
    return value


# Audio and video Live can reference. Anything else in a FileRef is a device,
# preset or Live's own content, none of which belongs in a project repository.
MEDIA_SUFFIXES = {
    ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma",
    ".mov", ".mp4", ".m4v", ".avi", ".rex", ".rx2",
}

# Content that ships with Live or with an installed Pack: present on every
# machine that has the same Live installed, so it must not be collected.
_FACTORY_MARKERS = (
    "/Core Library/",
    "/Factory Packs/",
    "/Ableton Folder Info/",
)


def _is_factory_content(relative: str, absolute: str, live_pack: str) -> bool:
    if live_pack:
        return True
    reference = absolute or relative
    if ".app/Contents/" in reference and "Ableton Live" in reference:
        return True
    if any(marker in reference for marker in _FACTORY_MARKERS):
        return True
    return Path(reference).suffix.lower() not in MEDIA_SUFFIXES


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _verb(n: int) -> str:
    return "lives" if n == 1 else "live"


def _first_few(paths: list[str], limit: int = 5) -> str:
    shown = "\n  ".join(paths[:limit])
    if len(paths) > limit:
        shown += f"\n  ...and {len(paths) - limit} more"
    return shown


@dataclass
class MediaReport:
    """Audio a Set uses, grouped by whether it would survive the trip."""

    inside: list[str] = field(default_factory=list)
    outside: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def is_self_contained(self) -> bool:
        return not self.outside and not self.missing


class AbletonAdapter:
    format_name = "ableton"
    adapter_version = 1
    #: the folder Live uses to find a project's root; kept even when empty
    project_marker = PROJECT_INFO_DIR

    # --- discovery -----------------------------------------------------

    def detect_project(self, path: Path) -> bool:
        path = Path(path)
        if not path.is_dir():
            return False
        return bool(self._live_sets(path)) or (path / PROJECT_INFO_DIR).is_dir()

    def inspect_project(self, path: Path) -> ProjectInfo:
        path = Path(path)
        sets = self._live_sets(path)
        primary = self._primary_set(path, sets)
        return ProjectInfo(
            root=path,
            name=path.name,
            primary_set=primary,
            all_sets=sets,
            live_version=self._live_version(primary) if primary else None,
        )

    def validate_project(self, path: Path) -> ValidationResult:
        path = Path(path)
        result = ValidationResult()
        sets = self._live_sets(path)

        if not sets:
            result.errors.append(
                "No Live Set (.als) found in this folder — it cannot be opened in Ableton."
            )
        elif len(sets) > 1:
            names = ", ".join(s.name for s in sets)
            result.warnings.append(
                f"This folder contains more than one Live Set ({names}). "
                "All of them will be synced."
            )

        if not (path / PROJECT_INFO_DIR).is_dir():
            result.warnings.append(
                "No 'Ableton Project Info' folder — this may not be a proper Ableton "
                "Project Folder. Use File > Save Live Set As and create a project folder."
            )

        media = self.referenced_media(path)
        if media.outside:
            count = _count(len(media.outside), "sample")
            result.warnings.append(
                f"{count} used by this project {_verb(len(media.outside))} outside the "
                "project folder. Push copies them into the shared copy's "
                "Samples/Imported folder for you.\n  "
                + _first_few(media.outside)
            )
        if media.missing:
            count = _count(len(media.missing), "sample")
            result.warnings.append(
                f"{count} used by this project cannot be found on this computer at all — "
                "the project is already incomplete here.\n  " + _first_few(media.missing)
            )

        return result

    def referenced_media(self, path: Path) -> MediaReport:
        """Where the audio a Set uses actually lives.

        Live records each sample twice: a path relative to the project folder
        and the absolute path it was imported from. Audio that only resolves
        through the absolute path exists on this computer alone — it is exactly what
        Collect All and Save would pull in, and what a collaborator would be
        missing.
        """
        path = Path(path)
        report = MediaReport()
        seen: set[str] = set()

        for live_set in self._live_sets(path):
            for relative, absolute, live_pack in self._file_refs(live_set):
                key = relative or absolute
                if not key or key in seen:
                    continue
                if _is_factory_content(relative, absolute, live_pack):
                    continue  # ships with Live or an installed Pack, not project audio
                seen.add(key)

                if relative and (path / relative).exists():
                    report.inside.append(relative)
                elif absolute and Path(absolute).exists():
                    report.outside.append(absolute)
                else:
                    report.missing.append(relative or absolute)

        for group in (report.inside, report.outside, report.missing):
            group.sort()
        return report

    def prepare_project(self, path: Path) -> list[str]:
        return [
            "In Ableton, open this Live Set.",
            "Choose File > Collect All and Save.",
            "Tick the categories you want copied in (recorded and imported audio at "
            "minimum; also Max for Live devices if you use them).",
            "Collect All and Save copies every sample the Set uses into the project "
            "folder, so collaborators are not missing audio that only exists on your computer.",
            "Save, then close the Set before pushing.",
        ]

    # --- file selection ------------------------------------------------

    def plan_push(self, path: Path, *, known=None, hash_file, rewrite: bool = True):
        """Consolidate: exactly what a Push of this project sends (see consolidate.py)."""
        from sessionsync.adapters.ableton.consolidate import plan_push

        path = Path(path)
        sets = self._live_sets(path)
        return plan_push(
            path,
            tracked=self.enumerate_tracked_files(path),
            main_set=self._primary_set(path, sets),
            known=known,
            hash_file=hash_file,
            rewrite=rewrite,
        )

    def enumerate_tracked_files(self, path: Path) -> list[Path]:
        path = Path(path)
        tracked = [p for p in path.rglob("*") if p.is_file() and self._is_tracked(p, path)]
        return sorted(tracked)

    def track_fingerprints(self, path: Path) -> dict[str, str] | None:
        """{track name: hash of that track's content} for the main Live Set.

        Lets a version be described as "changed Drums" instead of "changed a
        file". None when the set cannot be read, so callers fall back to files.
        """
        info = self.inspect_project(path)
        if info.primary_set is None:
            return None
        try:
            with gzip.open(info.primary_set, "rb") as fh:
                root = ET.fromstring(fh.read())
        except (OSError, EOFError, ET.ParseError):
            return None
        tracks_el = root.find("./LiveSet/Tracks")
        if tracks_el is None:
            return None
        found: dict[str, str] = {}
        for track in tracks_el:
            if track.tag not in _TRACK_TAGS:
                continue
            name_el = track.find("./Name/EffectiveName")
            name = (name_el.get("Value") if name_el is not None else "") or track.tag
            unique, n = name, 2
            while unique in found:  # two tracks can share a name
                unique, n = f"{name} {n}", n + 1
            found[unique] = _digest([track])
        # The Main track holds tempo and time-signature automation: a tempo change
        # is a real change, and it lives outside <Tracks>.
        main = root.find("./LiveSet/MasterTrack")
        if main is None:
            main = root.find("./LiveSet/MainTrack")
        if main is not None:
            found[MAIN_TRACK] = _digest([main])
        song = [el for tag in _SONG_TAGS if (el := root.find(f"./LiveSet/{tag}")) is not None]
        if song:
            found[SONG_PARTS] = _digest(song)
        return found

    def environment_requirements(self, path: Path) -> dict:
        info = self.inspect_project(path)
        return {"daw": "Ableton Live", "live_version": info.live_version, "plugins": []}

    # --- internals -----------------------------------------------------

    def _is_tracked(self, file: Path, root: Path) -> bool:
        rel_parts = file.relative_to(root).parts
        if any(part in EXCLUDED_DIRS or part.startswith(".sessionsync") for part in rel_parts[:-1]):
            return False  # incl. ".sessionsync-removed …", set aside by Remove from Session Sync
        name = file.name
        if name in EXCLUDED_NAMES or name.startswith("._"):
            return False
        if file.suffix in EXCLUDED_SUFFIXES:
            return False
        return True

    def _live_sets(self, path: Path) -> list[Path]:
        return sorted(
            p
            for p in path.rglob("*.als")
            if self._is_tracked(p, path)
        )

    def _primary_set(self, path: Path, sets: list[Path]) -> Path | None:
        if not sets:
            return None
        # A set named after its folder is Ableton's own convention for the project.
        for candidate in sets:
            if candidate.stem == path.name and candidate.parent == path:
                return candidate
        top_level = [s for s in sets if s.parent == path]
        return (top_level or sets)[0]

    def _file_refs(self, live_set: Path) -> list[tuple[str, str, str]]:
        """(relative path, absolute path, Live Pack name) for every referenced file."""
        try:
            with gzip.open(live_set, "rb") as fh:
                xml = fh.read()
        except (OSError, EOFError):
            return []

        refs = []
        for block in _FILE_REF_RE.finditer(xml):
            chunk = block.group(0)
            relative = _attr(chunk, b"RelativePath")
            absolute = _attr(chunk, b"Path")
            if relative or absolute:
                refs.append((relative, absolute, _attr(chunk, b"LivePackName")))
        return refs

    def _live_version(self, live_set: Path) -> str | None:
        """Live Sets are gzipped XML; the version lives in the root element."""
        try:
            with gzip.open(live_set, "rb") as fh:
                head = fh.read(4096)
        except (OSError, EOFError):
            return None
        match = _MINOR_VERSION_RE.search(head)
        if not match:
            return None
        return (match.group(1) or match.group(2)).decode("utf-8", "replace")
