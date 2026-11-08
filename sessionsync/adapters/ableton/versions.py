"""Live 10, 11 and 12 on one song.

A song's shared copy stays in one Live version (the song's version, usually the
oldest anyone uses). Each computer gets its sets in *its* Live version, and a
Push turns them back into the song's version. Live itself only reads older sets
and only writes its own version, so Session Sync does the translation.

How: a Live Set is gzipped XML. Between versions Live renames a few elements,
adds new settings and drops old ones. A *schema* for a Live version records, for
every element (keyed by its tag path, restarting at each device so a device looks
the same wherever it sits): which attributes and children it may have, in what
order, what kind of value it holds, and a small default copy of it. Converting a
set to that version:

- renames the elements Live renamed,
- drops elements and attributes that version does not know (lost features are
  listed so the user can be told),
- adds the elements that version expects but the set lacks, copied from the
  schema's defaults (with fresh pointee ids so Live does not see duplicates),
- recasts values whose kind changed (true/false <-> 0/1),
- takes the version header (MinorVersion, Creator...) from that version.

Schemas for Live 10 and 11 ship with the app (learned from Live's own presets and
real sets by scripts/build_live_schema.py). The schema for the Live installed on
this computer can also be learned on the spot from its Core Library.

Sample references: every <FileRef> also says whether its path is a Windows or a
Mac path. A set saved on Windows opened on a Mac finds none of its samples, so
every set a Pull brings in is "localized": its references re-pointed at this
computer's project folder, in this computer's style.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_DIR = Path(__file__).with_name("schemas")
LOCALIZE = True  # tests switch this off unless they are about it
_MINOR_RE = re.compile(rb"MinorVersion=[\"'](\d+)\.")

# Elements Live renamed: newer name -> older name, per newer major.
RENAMES = {
    12: {
        "MainTrack": "MasterTrack",
        "IsSongTempoLeader": "IsSongTempoMaster",
        "ReWireDeviceMidiTargetId": "ReWireSlaveMidiTargetId",
        "AutoColorPickerForReturnAndMainTracks": "AutoColorPickerForReturnAndMasterTracks",
    },
}
# Renamed in some places only: applied where the target version has the other name.
SOFT_RENAMES = {"Color": "ColorIndex", "ColorIndex": "Color"}

# Live 12 stores a clip's scale as a number; Live 11 by name.
SCALES = [
    "Major", "Minor", "Dorian", "Mixolydian", "Lydian", "Phrygian", "Locrian", "Diminished",
    "Whole-half", "Whole Tone", "Minor Blues", "Minor Pentatonic", "Major Pentatonic",
    "Harmonic Minor", "Melodic Minor", "Super Locrian", "Bhairav", "Hungarian Minor",
    "Minor Gypsy", "Hirojoshi", "In-Sen", "Iwato", "Kumoi", "Pelog", "Spanish",
]

# Dropped elements worth telling the user about: tag -> what it is.
NOTABLE = {
    "TakeLanes": "take lanes (comping)",
    "TuningSystem": "a tuning system",
    "TuningSystems": "a tuning system",
    "NoteProbabilityGroups": "note probability groups",
    "MpeSettings": "MPE settings",
    "Groove": "a groove",
}

HEADER_ATTRS = ("MajorVersion", "MinorVersion", "SchemaChangeCount", "Creator", "Revision")
RESTART_PARENTS = {"Devices", "Device"}
WINDOWS_PATH, MAC_PATH = "1", "2"  # <FileRef><Type>: how its Path is written

_INT = re.compile(r"-?\d+$")
_FLOAT = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?$")


class ConversionError(Exception):
    """A set could not be converted between Live versions."""


# --- reading versions ---------------------------------------------------------


def live_major(data: bytes) -> int | None:
    """The Live major version that saved this set (gzipped or plain XML), or None."""
    head = data[:4096]
    if head[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(data)) as f:
                head = f.read(4096)
        except OSError:
            return None
    m = _MINOR_RE.search(head)
    return int(m.group(1)) if m else None


def set_major(path: Path) -> int | None:
    try:
        with gzip.open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = _MINOR_RE.search(head)
    return int(m.group(1)) if m else None


# --- which Live this computer uses --------------------------------------------

_preferred: int | None = None


def set_preferred_major(major: int | None) -> None:
    """The Live version the user chose in Settings (None = newest installed)."""
    global _preferred
    _preferred = major


def installed_lives() -> dict[int, list[Path]]:
    """{major: [install folders]} for every Live on this computer."""
    found: dict[int, list[Path]] = defaultdict(list)
    if sys.platform == "darwin":
        roots = [Path("/Applications"), Path.home() / "Applications"]
        pattern = "Ableton Live *.app"
    elif os.name == "nt":
        roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Ableton"]
        pattern = "Live *"
    else:
        return {}
    for root in roots:
        try:
            for app in root.glob(pattern):
                m = re.search(r"Live (\d+)", app.name)
                if m:
                    found[int(m.group(1))].append(app)
        except OSError:
            continue
    return dict(found)


def my_live_major() -> int | None:
    """The Live version this computer makes music in, or None if unknown."""
    env = os.environ.get("SESSIONSYNC_LIVE_MAJOR")
    if env and env.isdigit():
        return int(env)
    if _preferred:
        return _preferred
    lives = installed_lives()
    return max(lives) if lives else None


# --- schemas ------------------------------------------------------------------


def _kind(value: str) -> str:
    if value in ("true", "false"):
        return "bool"
    if _INT.match(value):
        return "int"
    if _FLOAT.match(value):
        return "float"
    return "str"


def _child_key(parent: ET.Element, key: tuple, child: ET.Element) -> tuple:
    if parent.tag in RESTART_PARENTS or (parent.tag == "Ableton" and child.tag != "LiveSet"):
        return ("@", child.tag)
    return key + (child.tag,)


@dataclass
class Schema:
    major: int
    header: dict = field(default_factory=dict)
    attrs: dict = field(default_factory=lambda: defaultdict(set))
    order: dict = field(default_factory=dict)        # struct key -> child tags in order
    listy: set = field(default_factory=set)          # keys whose children vary
    kinds: dict = field(default_factory=lambda: defaultdict(set))
    counts: Counter = field(default_factory=Counter)
    templates: dict = field(default_factory=dict)    # key -> small default element (bytes)
    _sets: dict = field(default_factory=lambda: defaultdict(set))

    # learning
    def learn(self, root: ET.Element, *, private: bool = False) -> None:
        """Learn from one file. `private`: someone's own set, so the defaults kept
        from it carry no names or paths."""
        if root.find("LiveSet") is not None and _release(root) >= _release_of(self.header):
            self.header = {a: root.get(a) for a in HEADER_ATTRS if root.get(a) is not None}
        stack = [(root, (root.tag,))]
        while stack:
            e, key = stack.pop()
            self.counts[key] += 1
            self.attrs[key].update(e.attrib)
            if "Value" in e.attrib:
                self.kinds[key].add(_kind(e.get("Value")))
            tags = [c.tag for c in e]
            if len(tags) != len(set(tags)):
                self.listy.add(key)
            self._sets[key].add(tuple(tags))
            if len(tags) > len(self.order.get(key, ())):
                self.order[key] = tuple(tags)
            if key not in self.templates and sum(1 for _ in e.iter()) <= 400:
                self.templates[key] = ET.tostring(_scrubbed(e) if private else e)
            for c in e:
                stack.append((c, _child_key(e, key, c)))

    def finish(self) -> Schema:
        for key, seen in self._sets.items():
            if len({frozenset(s) for s in seen}) > 1:
                self.listy.add(key)
        self._sets = defaultdict(set)
        return self

    # storage
    def to_json(self) -> bytes:
        needed = {
            k + (t,) for k, order in self.order.items() if k not in self.listy for t in order
        }
        data = {
            "major": self.major,
            "header": self.header,
            "keys": {
                "/".join(k): [
                    sorted(self.attrs.get(k, ())),
                    None if k in self.listy else list(self.order.get(k, ())),
                    sorted(self.kinds.get(k, ())),
                ]
                for k in self.counts
            },
            "templates": {"/".join(k): v.decode() for k, v in self.templates.items() if k in needed},
        }
        return gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9, mtime=0)

    @classmethod
    def from_json(cls, raw: bytes) -> Schema:
        data = json.loads(gzip.decompress(raw))
        s = cls(major=data["major"], header=data["header"])
        for k, (attrs, order, kinds) in data["keys"].items():
            key = tuple(k.split("/"))
            s.counts[key] = 1
            s.attrs[key] = set(attrs)
            if order is None:
                s.listy.add(key)
            else:
                s.order[key] = tuple(order)
            s.kinds[key] = set(kinds)
        s.templates = {tuple(k.split("/")): v.encode() for k, v in data["templates"].items()}
        return s


def _release(root: ET.Element) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", root.get("Creator", "")))


def _release_of(header: dict) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", header.get("Creator", "")))


def _scrubbed(e: ET.Element) -> ET.Element:
    import copy

    e = copy.deepcopy(e)
    for x in e.iter():
        for name, value in list(x.attrib.items()):
            if _kind(value) == "str":
                x.set(name, "")
        if x.text and x.text.strip():
            x.text = ""
    return e


def learn_schema(major: int, files, private=()) -> Schema:
    """Learn from Live's own files first (their defaults are kept as they are),
    then `private` files (people's sets: structure only, no names or paths)."""
    schema = Schema(major=major)
    for f, is_private in [(f, False) for f in files] + [(f, True) for f in private]:
        try:
            with gzip.open(f, "rb") as fh:
                root = ET.fromstring(fh.read())
        except (OSError, ET.ParseError, EOFError):
            continue
        if not root.get("MinorVersion", "").startswith(f"{major}."):
            continue
        schema.learn(root, private=is_private)
    return schema.finish()


_cache: dict[int, Schema | None] = {}


def schema_for(major: int) -> Schema | None:
    """The schema for a Live version: shipped, cached, or learned from this computer's Live."""
    if major in _cache:
        return _cache[major]
    schema = None
    shipped = SCHEMA_DIR / f"live{major}.json.gz"
    if shipped.exists():
        schema = Schema.from_json(shipped.read_bytes())
    else:
        cached = _cache_dir() / f"live{major}.json.gz"
        if cached.exists():
            try:
                schema = Schema.from_json(cached.read_bytes())
            except (OSError, ValueError):
                schema = None
        if schema is None:
            files = _installed_corpus(major)
            if files:
                schema = learn_schema(major, files)
                if schema.counts:
                    try:
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        cached.write_bytes(schema.to_json())
                    except OSError:
                        pass
                else:
                    schema = None
    _cache[major] = schema
    return schema


def _cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "SessionSync"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SessionSync" / "cache"
    return Path.home() / ".cache" / "sessionsync"


def _installed_corpus(major: int, limit: int = 1500) -> list[Path]:
    files: list[Path] = []
    for install in installed_lives().get(major, []):
        for sub in ("Contents/App-Resources", "Resources"):
            base = install / sub
            if base.is_dir():
                for pattern in ("*.als", "*.adg", "*.adv"):
                    files.extend(sorted(base.rglob(pattern))[: limit // 3])
    return files[:limit]


# --- converting ---------------------------------------------------------------


@dataclass
class Converted:
    data: bytes
    from_major: int
    to_major: int
    lost: list[str]          # plain words: what the target Live cannot hold


def convert(data: bytes, to_major: int, schema: Schema | None = None) -> Converted:
    """Turn a set (gzipped bytes) into one `to_major` Live can open."""
    root = _parse(data)
    from_major = int(root.get("MinorVersion", "0").split(".")[0] or 0)
    if from_major == to_major:
        return Converted(data, from_major, to_major, [])
    schema = schema or schema_for(to_major)
    if schema is None:
        raise ConversionError(f"Session Sync does not know Live {to_major}'s set format.")
    lost = _convert_tree(root, schema, from_major, to_major)
    return Converted(_serialize(root), from_major, to_major, lost)


def _parse(data: bytes) -> ET.Element:
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        return ET.fromstring(raw)
    except (OSError, ET.ParseError, EOFError) as exc:
        raise ConversionError(f"The set could not be read ({exc}).") from exc


def _serialize(root: ET.Element) -> bytes:
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return gzip.compress(xml, 9, mtime=0)


def _convert_tree(root: ET.Element, schema: Schema, from_major: int, to_major: int) -> list[str]:
    down = to_major < from_major
    renames: dict[str, str] = {}
    if down:
        for major in range(from_major, to_major, -1):
            renames.update(RENAMES.get(major, {}))
    else:
        for major in range(to_major, from_major, -1):
            renames.update({new_name: old for old, new_name in RENAMES.get(major, {}).items()})
    for e in root.iter():
        if e.tag in renames:
            e.tag = renames[e.tag]
    for name, value in schema.header.items():
        root.set(name, value)
    if to_major <= 10 < from_major:
        _filerefs_to_live10(root)

    lost: list[str] = []
    used = [int(x.get("Id")) for x in root.iter() if (x.get("Id") or "").isdigit()]
    npi = root.find("LiveSet/NextPointeeId")
    start = int(npi.get("Value")) if npi is not None and (npi.get("Value") or "").isdigit() else 0
    next_id = [max([start, *used]) + 1]

    def fresh_ids(element: ET.Element) -> None:
        for x in element.iter():
            if "Id" in x.attrib and (x.tag.endswith("Target") or x.tag == "Pointee"):
                x.set("Id", str(next_id[0]))
                next_id[0] += 1

    def note_loss(parent: ET.Element, child: ET.Element) -> None:
        if parent.tag == "Devices":
            lost.append(f"the {child.tag} device")
        elif child.tag in NOTABLE and len(child):
            lost.append(NOTABLE[child.tag])

    def fix(e: ET.Element, key: tuple) -> None:
        if "Value" in e.attrib:
            _recast(e, key, schema)
        for name in list(e.attrib):
            if name not in schema.attrs.get(key, ()) and (down or key in schema.counts):
                del e.attrib[name]
        for child in list(e):
            ck = _child_key(e, key, child)
            if ck not in schema.counts and child.tag in SOFT_RENAMES:
                alt = ck[:-1] + (SOFT_RENAMES[child.tag],)
                if alt in schema.counts:
                    child.tag, ck = SOFT_RENAMES[child.tag], alt
            if ck not in schema.counts:
                # Going up, a thin learned schema must not throw real work away:
                # only drop what a well-known parent clearly does not hold.
                if down or (key in schema.order and key not in schema.listy):
                    note_loss(e, child)
                    e.remove(child)
                continue
            fix(child, ck)
        if key in schema.listy or key not in schema.order:
            return
        want = schema.order[key]
        have = {c.tag for c in e}
        for tag in want:
            if tag not in have and (key + (tag,)) in schema.templates:
                added = ET.fromstring(schema.templates[key + (tag,)])
                fresh_ids(added)
                e.append(added)
        position = {t: i for i, t in enumerate(want)}
        kids = sorted(e, key=lambda c: position.get(c.tag, len(position)))
        for c in list(e):
            e.remove(c)
        e.extend(kids)

    fix(root, (root.tag,))
    if npi is not None:
        npi.set("Value", str(next_id[0]))
    seen, out = set(), []
    for item in lost:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _recast(e: ET.Element, key: tuple, schema: Schema) -> None:
    want = schema.kinds.get(key)
    value = e.get("Value")
    have = _kind(value)
    if not want or have in want or (have == "int" and "float" in want):
        return
    new = None
    if "int" in want and "bool" not in want:
        if have == "bool":
            new = "1" if value == "true" else "0"
        elif have == "float":
            new = str(int(round(float(value))))
    elif want == {"bool"} and have in ("int", "float"):
        new = "true" if float(value) != 0 else "false"
    elif "float" in want and have == "bool":
        new = "1" if value == "true" else "0"
    if new is None and key[-2:] == ("ScaleInformation", "Name") and have == "int":
        i = int(value)
        new = SCALES[i] if 0 <= i < len(SCALES) else "Major"
    if new is None and want == {"int"} and key[-2:] == ("ScaleInformation", "Name") and value in SCALES:
        new = str(SCALES.index(value))
    if new is None and key in schema.templates:
        new = ET.fromstring(schema.templates[key]).get("Value")
    if new is not None:
        e.set("Value", new)


def _filerefs_to_live10(root: ET.Element) -> None:
    """Live 11+ keeps sample paths as text; Live 10 as folder elements plus an
    alias (which it can do without when the relative path is there)."""
    for fr in root.iter("FileRef"):
        if fr.find("HasRelativePath") is not None:
            continue

        def val(tag, default=""):
            x = fr.find(tag)
            return x.get("Value", default) if x is not None else default

        rtype, rel, path = val("RelativePathType", "0"), val("RelativePath"), val("Path")
        typ, pack, pack_id = val("Type", MAC_PATH), val("LivePackName"), val("LivePackId")
        size, crc = val("OriginalFileSize", "0"), val("OriginalCrc", "0")
        name = (rel or path).replace("\\", "/").rstrip("/").split("/")[-1]
        dirs = (rel or "").replace("\\", "/").split("/")[:-1]
        for c in list(fr):
            fr.remove(c)

        def add(parent, tag, value=None):
            x = ET.SubElement(parent, tag)
            if value is not None:
                x.set("Value", value)
            return x

        add(fr, "HasRelativePath", "true" if rel else "false")
        add(fr, "RelativePathType", rtype if rel else "0")
        rp = add(fr, "RelativePath")
        for i, d in enumerate(dirs):
            x = ET.SubElement(rp, "RelativePathElement")
            x.set("Id", str(i))
            x.set("Dir", d)
        add(fr, "Name", name)
        add(fr, "Type", typ)
        add(fr, "Data")
        add(fr, "RefersToFolder", "false")
        hint = add(fr, "SearchHint")
        add(hint, "PathHint")
        add(hint, "FileSize", size)
        add(hint, "Crc", crc)
        add(hint, "MaxCrcSize", "16384")
        add(hint, "HasExtendedInfo", "true")
        add(fr, "LivePackName", pack)
        add(fr, "LivePackId", pack_id)


# --- making a set at home on this computer ------------------------------------


PROJECT_RELATIVE, SET_RELATIVE = "3", "1"


def localize(data: bytes, set_path: Path, project_root: Path, *, windows: bool | None = None) -> bytes:
    """Point a set's sample references at this computer's project folder, written
    the way this computer's Live expects. Returns the data unchanged if nothing
    needed changing."""
    windows = (os.name == "nt") if windows is None else windows
    want_type = WINDOWS_PATH if windows else MAC_PATH
    root = _parse(data)
    changed = False
    for fr in root.iter("FileRef"):
        typ, rtype, rel, path = (fr.find(t) for t in ("Type", "RelativePathType", "RelativePath", "Path"))
        if typ is not None and typ.get("Value") in (WINDOWS_PATH, MAC_PATH) and typ.get("Value") != want_type:
            typ.set("Value", want_type)
            changed = True
        if rtype is None or rel is None or path is None or not rel.get("Value"):
            continue  # Live 10 style, or no relative path: leave to Live
        base = {PROJECT_RELATIVE: project_root, SET_RELATIVE: set_path.parent}.get(rtype.get("Value"))
        if base is None:
            continue
        joined = os.path.normpath(os.path.join(str(base), *rel.get("Value").replace("\\", "/").split("/")))
        if windows:
            joined = joined.replace("\\", "/")  # Live on Windows writes C:/Users/...
        if path.get("Value") != joined:
            path.set("Value", joined)
            changed = True
    return _serialize(root) if changed else data


def lost_features(data: bytes, to_major: int) -> list[str]:
    """What a set would lose by becoming a `to_major` set (empty if nothing notable)."""
    try:
        return convert(data, to_major).lost
    except ConversionError:
        return []
