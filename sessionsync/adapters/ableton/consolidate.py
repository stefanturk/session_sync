"""Consolidate: Session Sync's own Collect All and Save, run on every Push.

A project folder holds far more than its Live Sets use: abandoned takes, old
bounces, samples tried once. Consolidate reads every Set and works out the
smallest set of files that reopens the project on another computer:

- audio and Max devices the Sets reference, from inside the project folder,
- the same from outside it (Downloads, a sample library), copied in under
  Samples/Imported/ exactly where Live's Collect All and Save would put them,
- the Sets themselves and small non-audio files (Ableton Project Info, notes).

Audio the Sets do not use is left on this computer and never uploaded.

The local Live Set is never written. When references need re-pointing (an
outside sample, a path relative to the Set rather than the project), only the
copy that goes to the shared folder is rewritten, and it is re-read and checked
before anything is uploaded. Only Live 11 and newer are rewritten: they store
paths as plain text. Live 10 hides them in a binary alias that cannot be edited
safely, so a Live 10 main Set has to be re-saved in a newer Live first.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sessionsync.core.errors import SessionSyncError
from sessionsync.core.manifest import normalize_path

PAST_REVISIONS_DIR = "Past Revisions"
IMPORTED_DIR = "Samples/Imported"

# Audio and video a Live Set can play. Unused files of these kinds are the bulk
# of a project folder, so they are the ones Consolidate leaves behind.
MEDIA_SUFFIXES = {
    ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma",
    ".mov", ".mp4", ".m4v", ".avi", ".rex", ".rx2",
}
DEVICE_SUFFIXES = {".amxd"}  # Max for Live devices: needed to play the song
HEAVY_SUFFIXES = MEDIA_SUFFIXES | DEVICE_SUFFIXES

_FILE_REF_RE = re.compile(rb"<FileRef>.*?</FileRef>", re.DOTALL)
_PARENT_RE = re.compile(rb"<(\w+)>\s*$")
_MINOR_RE = re.compile(rb"MinorVersion=[\"']([^\"']+)[\"']")
_VALUE_RE = {
    tag: re.compile(rb"<" + tag + rb'\s+Value="([^"]*)"')
    for tag in (
        b"RelativePathType", b"RelativePath", b"Path", b"LivePackName",
        b"LivePackId", b"OriginalFileSize",
    )
}

PROJECT_RELATIVE = "3"  # RelativePathType: relative to the project folder
SET_RELATIVE = "1"      # relative to the folder the Set is in
LIVE_LIBRARY = "5"      # Live's own Core Library


class ConsolidateError(SessionSyncError):
    """The project cannot be pushed safely as it is; the message says why."""


@dataclass
class Reference:
    """One file a Live Set needs, and where Consolidate found it."""

    set_path: str        # project-relative Set it came from
    start: int           # byte span of its <FileRef> block in the Set's XML
    end: int
    ref_type: str
    relative: str
    absolute: str
    kind: str = ""       # inside | outside | pack | missing
    source: Path | None = None   # the file on this computer
    shared: str = ""     # where it lives in the shared project
    pack: str = ""


@dataclass
class LiveSet:
    path: Path
    rel: str
    xml: bytes
    major: int | None
    refs: list[Reference] = field(default_factory=list)


@dataclass
class PushPlan:
    """What a Push would send, and everything worth telling the person first."""

    #: shared path -> the file on this computer that holds its content
    files: dict[str, Path] = field(default_factory=dict)
    #: shared path -> Set bytes as they go to the shared folder, when they had to change
    rewritten: dict[str, bytes] = field(default_factory=dict)
    main_set: str | None = None
    imported: dict[str, Path] = field(default_factory=dict)   # shared path -> outside file
    unused: list[str] = field(default_factory=list)
    unused_bytes: int = 0
    missing: list[str] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)
    skipped_sets: list[str] = field(default_factory=list)
    #: reasons Push must stop; empty means it is safe to go
    problems: list[str] = field(default_factory=list)

    @property
    def push_bytes(self) -> int:
        return sum(_size(p) for p in self.files.values())

    @property
    def imported_bytes(self) -> int:
        return sum(_size(p) for p in self.imported.values())

    def check(self) -> None:
        if self.problems:
            raise ConsolidateError("\n\n".join(self.problems))


HashFn = Callable[[Path], str]


def plan_push(
    project: Path,
    *,
    tracked: list[Path],
    main_set: Path | None,
    known: Mapping[str, str] | None = None,
    hash_file: HashFn,
    rewrite: bool = True,
) -> PushPlan:
    """Work out exactly what a Push sends. Reads the project; writes nothing.

    `tracked` is every candidate file (temp files and Backup/ already removed).
    `known` maps every path the shared copy has ever held to its content hash,
    so files copied in from outside keep one stable name across revisions and a
    new file never takes the name of an old one.
    With `rewrite` false (status checks) Sets are classified but not rewritten.
    """
    project = Path(project)
    known = dict(known or {})
    plan = PushPlan()

    tracked = [p for p in tracked if PAST_REVISIONS_DIR not in _rel(project, p).split("/")[:-1]]
    sets = [p for p in tracked if p.suffix.lower() == ".als"]
    main_rel = _rel(project, main_set) if main_set else None
    plan.main_set = main_rel

    live_sets: list[LiveSet] = []
    for path in sets:
        rel = _rel(project, path)
        live_set = _read_set(path, rel)
        if live_set is None:
            plan.problems.append(
                f"{rel} could not be read. Open it in Live and save it again, then Push."
            )
            plan.files[rel] = path  # status still shows it; Push stops
            continue
        if live_set.major is not None and live_set.major < 11:
            if rel == main_rel:
                plan.problems.append(
                    f"{rel} was saved by Live {live_set.major}. Open it in Live 11 or "
                    "newer and save it once, then Push."
                )
                plan.files[rel] = path
            else:
                plan.skipped_sets.append(rel)
            continue
        live_sets.append(live_set)
        plan.files[rel] = path

    # 1. every file the Sets need, found
    used_inside: set[str] = set()
    outside: dict[Path, list[Reference]] = {}
    packs: set[str] = set()
    missing: set[str] = set()
    for live_set in live_sets:
        for ref in _references(live_set):
            _resolve(project, live_set, ref)
            if ref.kind == "pack":
                packs.add(ref.pack)
            elif ref.kind == "missing":
                shown = ref.relative if ref.relative and not ref.relative.startswith("..") else ref.absolute
                missing.add(shown or ref.relative)
            elif ref.kind == "inside":
                used_inside.add(ref.shared)
                plan.files[ref.shared] = ref.source
            else:
                outside.setdefault(ref.source, []).append(ref)
            live_set.refs.append(ref)

    # 2. files from outside the project get a stable home under Samples/Imported
    if outside:
        taken = {normalize_path(p): sha for p, sha in known.items()}
        for path in tracked:
            taken.setdefault(_rel(project, path), "")  # a local file already owns that name
        by_hash = {sha: p for p, sha in known.items() if p.startswith(IMPORTED_DIR + "/")}
        for source in sorted(outside):
            sha = hash_file(source)
            shared = by_hash.get(sha) or _free_imported_name(source.name, sha, taken)
            taken[shared] = sha
            by_hash[sha] = shared
            plan.files[shared] = source
            plan.imported[shared] = source
            for ref in outside[source]:
                ref.shared = shared

    # 3. everything else in the folder: small files travel, unused audio stays here
    for path in tracked:
        rel = _rel(project, path)
        if rel in plan.files or path.suffix.lower() == ".als":
            continue
        if path.suffix.lower() in HEAVY_SUFFIXES:
            plan.unused.append(rel)
            plan.unused_bytes += _size(path)
        else:
            plan.files[rel] = path

    plan.missing = sorted(missing)
    plan.packs = sorted(packs)
    plan.unused.sort()

    # 4. re-point references in the copies that go to the shared folder
    if rewrite and not plan.problems:
        for live_set in live_sets:
            new_bytes = _rewrite(project, live_set)
            if new_bytes is None:
                continue
            try:
                _verify(live_set, new_bytes, plan)
            except ConsolidateError as exc:
                plan.problems.append(str(exc))
                continue
            plan.rewritten[live_set.rel] = new_bytes
    return plan


# --- reading a Set ---------------------------------------------------------


def _read_set(path: Path, rel: str) -> LiveSet | None:
    try:
        with gzip.open(path, "rb") as fh:
            xml = fh.read()
    except (OSError, EOFError):
        return None
    head = xml[:4096]
    if b"<Ableton" not in head:
        return None
    match = _MINOR_RE.search(head)
    major = None
    if match:
        try:
            major = int(match.group(1).split(b".")[0])
        except ValueError:
            major = None
    return LiveSet(path=path, rel=rel, xml=xml, major=major)


def _value(chunk: bytes, tag: bytes) -> str:
    match = _VALUE_RE[tag].search(chunk)
    if not match:
        return ""
    return _unescape(match.group(1).decode("utf-8", "replace"))


def _references(live_set: LiveSet) -> list[Reference]:
    """The FileRefs a Set needs to play.

    Live also records where each device preset was loaded from; those FileRefs
    are history, not dependencies, and are skipped. What a Set needs is the
    audio inside a <SampleRef> and any Max for Live device.
    """
    refs = []
    xml = live_set.xml
    for block in _FILE_REF_RE.finditer(xml):
        chunk = block.group(0)
        relative = _value(chunk, b"RelativePath")
        absolute = _value(chunk, b"Path")
        name = absolute or relative
        if not name:
            continue
        parent = _PARENT_RE.search(xml[max(0, block.start() - 64):block.start()])
        in_sample = parent is not None and parent.group(1) == b"SampleRef"
        suffix = PurePosixPath(name).suffix.lower()
        if not in_sample and suffix not in DEVICE_SUFFIXES:
            continue
        ref = Reference(
            set_path=live_set.rel,
            start=block.start(),
            end=block.end(),
            ref_type=_value(chunk, b"RelativePathType"),
            relative=relative,
            absolute=absolute,
        )
        ref.pack = _value(chunk, b"LivePackName")
        ref.kind = "pack" if _is_ableton_content(chunk, ref) else ""
        refs.append(ref)
    return refs


def _is_ableton_content(chunk: bytes, ref: Reference) -> bool:
    """Content every collaborator already has with Live: never copied."""
    pack_id = _value(chunk, b"LivePackId")
    if ref.pack and pack_id.startswith("www.ableton.com"):
        return True
    reference = ref.absolute or ref.relative
    if ref.ref_type == LIVE_LIBRARY or (".app/Contents/" in reference and "Ableton Live" in reference):
        ref.pack = ref.pack or "Core Library"
        return True
    if "/Core Library/" in reference or "/Factory Packs/" in reference:
        ref.pack = ref.pack or "Core Library"
        return True
    return False


def _resolve(project: Path, live_set: LiveSet, ref: Reference) -> None:
    if ref.kind == "pack":
        return
    candidates: list[Path] = []
    if ref.relative and ref.ref_type == PROJECT_RELATIVE:
        candidates.append(project / ref.relative)
    elif ref.relative and ref.ref_type == SET_RELATIVE:
        candidates.append(live_set.path.parent / ref.relative)
    if ref.absolute:
        candidates.append(Path(ref.absolute))
    if ref.relative and ref.ref_type not in (PROJECT_RELATIVE, SET_RELATIVE):
        candidates.append(project / ref.relative)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        real = Path(os.path.normpath(candidate))
        try:
            inside = real.resolve().relative_to(project.resolve())
        except ValueError:
            ref.kind, ref.source = "outside", real
            return
        ref.kind, ref.source = "inside", real
        ref.shared = normalize_path(inside.as_posix())
        return
    ref.kind = "missing"


def _free_imported_name(name: str, sha: str, taken: Mapping[str, str]) -> str:
    stem, suffix = os.path.splitext(name)
    candidate, n = f"{IMPORTED_DIR}/{name}", 2
    while candidate in taken and taken[candidate] != sha:
        candidate = f"{IMPORTED_DIR}/{stem} ({n}){suffix}"
        n += 1
    return normalize_path(candidate)


# --- rewriting the shared copy ----------------------------------------------


def _rewrite(project: Path, live_set: LiveSet) -> bytes | None:
    """The Set's bytes with every needed reference pointing inside the project.

    Only three values inside each affected <FileRef> change; every other byte
    of the Set is kept exactly. None when nothing needs changing.
    """
    edits = []
    for ref in live_set.refs:
        if ref.kind not in ("inside", "outside"):
            continue
        if ref.ref_type == PROJECT_RELATIVE and normalize_path(ref.relative) == ref.shared:
            continue
        chunk = live_set.xml[ref.start:ref.end]
        new_chunk = _set_value(chunk, b"RelativePathType", PROJECT_RELATIVE)
        new_chunk = _set_value(new_chunk, b"RelativePath", ref.shared)
        new_chunk = _set_value(new_chunk, b"Path", str(project / ref.shared))
        edits.append((ref.start, ref.end, new_chunk))
    if not edits:
        return None
    out = io.BytesIO()
    position = 0
    for start, end, new_chunk in sorted(edits):
        out.write(live_set.xml[position:start])
        out.write(new_chunk)
        position = end
    out.write(live_set.xml[position:])
    return gzip.compress(out.getvalue(), mtime=0)


def _set_value(chunk: bytes, tag: bytes, value: str) -> bytes:
    encoded = _escape(value).encode("utf-8")
    pattern = _VALUE_RE[tag]
    if pattern.search(chunk):
        return pattern.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + encoded + b'"', chunk, count=1)
    # a FileRef without this tag: add it right after <FileRef>
    return chunk.replace(b"<FileRef>", b"<FileRef><" + tag + b' Value="' + encoded + b'" />', 1)


def _verify(live_set: LiveSet, new_bytes: bytes, plan: PushPlan) -> None:
    """Re-read the rewritten Set as a collaborator's Live would, before uploading.

    It must still be well-formed XML, and every reference must now point inside
    the shared project at a file this Push is sending.
    """
    problem = f"Could not safely prepare {live_set.rel} for sharing"
    try:
        xml = gzip.decompress(new_bytes)
        for _ in ET.iterparse(io.BytesIO(xml), events=("end",)):
            pass
    except (OSError, EOFError, ET.ParseError) as exc:
        raise ConsolidateError(f"{problem} (the rewritten Set did not read back: {exc}).") from None

    again = LiveSet(path=live_set.path, rel=live_set.rel, xml=xml, major=live_set.major)
    before = [r for r in live_set.refs]
    after = _references(again)
    if len(after) != len(before):
        raise ConsolidateError(f"{problem} (it lost track of some samples).")
    for old, new in zip(before, after):
        if old.kind not in ("inside", "outside"):
            continue
        if new.ref_type != PROJECT_RELATIVE or normalize_path(new.relative) != old.shared:
            raise ConsolidateError(f"{problem} ({old.shared} was not re-pointed).")
        source = plan.files.get(old.shared)
        if source is None or source != old.source or not Path(source).is_file():
            raise ConsolidateError(f"{problem} ({old.shared} is not being sent).")


# --- small helpers ------------------------------------------------------------


def _rel(project: Path, path: Path) -> str:
    return normalize_path(Path(path).relative_to(project).as_posix())


def _size(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _unescape(value: str) -> str:
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        value = value.replace(entity, char)
    return value


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
