"""Manifests: the complete description of one version of a project.

A manifest maps every tracked project-relative path to the hash of its content.
Versions are compared by diffing manifests, and objects are uploaded by hash, so
identical audio shared between versions is stored once.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sessionsync.core.hashing import HashCache

MANIFEST_FORMAT_VERSION = 1


def normalize_path(rel_path: str) -> str:
    """Project-relative, POSIX separators, NFC.

    macOS reports filenames decomposed (NFD); without normalising, the same file
    looks renamed after a round trip through another machine.
    """
    return unicodedata.normalize("NFC", str(rel_path).replace("\\", "/"))


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int
    #: For a Live Set that Push rewrote (Consolidate re-pointed its samples):
    #: the hash of the untouched Set on the computer that pushed it. That copy
    #: counts as "the same as the shared one", so it is neither shown as changed
    #: nor downloaded again.
    source_sha256: str | None = None

    def to_dict(self) -> dict:
        data = {"sha256": self.sha256, "size": self.size}
        if self.source_sha256:
            data["source_sha256"] = self.source_sha256
        return data

    def matches(self, sha256: str) -> bool:
        """Is a local file with this hash this entry's content?"""
        return sha256 == self.sha256 or (self.source_sha256 is not None and sha256 == self.source_sha256)


@dataclass
class Manifest:
    version: int
    repository_id: str
    files: dict[str, FileEntry] = field(default_factory=dict)
    parent_version: int | None = None
    project_format: str = "ableton"
    created_at: str | None = None
    author: dict | None = None
    note: str | None = None
    #: Optional extras written at push time. Older manifests lack them, and
    #: readers that do not know them ignore them, so no format bump is needed.
    summary: str | None = None
    tracks: dict[str, str] | None = None
    #: The main Live Set saved into Past Revisions/ by this push:
    #: {"path", "sha256", "size", "set"}. Not part of `files` — it is never
    #: edited, so it never shows up as a change.
    revision: dict | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files.values())

    def hashes(self) -> set[str]:
        return {entry.sha256 for entry in self.files.values()}

    def to_json(self) -> str:
        payload = {
            "manifest_format_version": MANIFEST_FORMAT_VERSION,
            "version": self.version,
            "repository_id": self.repository_id,
            "parent_version": self.parent_version,
            "project_format": self.project_format,
            "created_at": self.created_at,
            "author": self.author,
            "note": self.note,
            "summary": self.summary,
            "tracks": self.tracks,
            "revision": self.revision,
            "files": {path: self.files[path].to_dict() for path in sorted(self.files)},
        }
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Manifest:
        data = json.loads(raw)
        files = {
            path: FileEntry(
                path=path,
                sha256=info["sha256"],
                size=info["size"],
                source_sha256=info.get("source_sha256"),
            )
            for path, info in data.get("files", {}).items()
        }
        return cls(
            version=data["version"],
            repository_id=data["repository_id"],
            files=files,
            parent_version=data.get("parent_version"),
            project_format=data.get("project_format", "ableton"),
            created_at=data.get("created_at"),
            author=data.get("author"),
            note=data.get("note"),
            summary=data.get("summary"),
            tracks=data.get("tracks"),
            revision=data.get("revision"),
        )


def build_file_map(
    project_root: Path,
    paths: Iterable[Path],
    hash_cache: HashCache,
) -> dict[str, FileEntry]:
    """Hash the given files and key them by their project-relative path."""
    project_root = Path(project_root)
    files: dict[str, FileEntry] = {}
    for path in paths:
        path = Path(path)
        rel = normalize_path(path.relative_to(project_root).as_posix())
        files[rel] = FileEntry(
            path=rel,
            sha256=hash_cache.hash_file(path),
            size=path.stat().st_size,
        )
    return files


@dataclass
class ManifestDiff:
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]
    added_bytes: int
    modified_bytes: int
    #: size of every added or modified file, so callers can show a change list
    #: without rescanning the project.
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def changed_bytes(self) -> int:
        return self.added_bytes + self.modified_bytes

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    def __bool__(self) -> bool:
        return self.has_changes


def diff_manifests(old: Manifest, new: Manifest) -> ManifestDiff:
    return diff_file_maps(old.files, new.files)


def diff_file_maps(
    old: Mapping[str, FileEntry],
    new: Mapping[str, FileEntry],
) -> ManifestDiff:
    added = sorted(set(new) - set(old))
    deleted = sorted(set(old) - set(new))
    common = set(old) & set(new)
    modified = sorted(p for p in common if old[p].sha256 != new[p].sha256)
    unchanged = sorted(p for p in common if old[p].sha256 == new[p].sha256)
    return ManifestDiff(
        added=added,
        modified=modified,
        deleted=deleted,
        unchanged=unchanged,
        added_bytes=sum(new[p].size for p in added),
        modified_bytes=sum(new[p].size for p in modified),
        sizes={p: new[p].size for p in added + modified},
    )
