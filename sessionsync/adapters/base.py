"""The DAW adapter interface.

Everything DAW-specific lives behind this. The repository engine never knows
whether it is syncing an Ableton project or, later, a Logic one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass
class ProjectInfo:
    root: Path
    name: str
    primary_set: Path | None = None
    all_sets: list[Path] = field(default_factory=list)
    live_version: str | None = None


@runtime_checkable
class DAWAdapter(Protocol):
    format_name: str
    adapter_version: int

    def detect_project(self, path: Path) -> bool:
        """Does this folder look like a project this adapter handles?"""

    def inspect_project(self, path: Path) -> ProjectInfo:
        """What is in it — name, main document, DAW version."""

    def validate_project(self, path: Path) -> ValidationResult:
        """Problems that would stop the project reopening elsewhere."""

    def prepare_project(self, path: Path) -> list[str]:
        """Human-readable steps to make the project self-contained."""

    def enumerate_tracked_files(self, path: Path) -> list[Path]:
        """Every file needed to recreate a working project."""

    def environment_requirements(self, path: Path) -> dict:
        """Plugins/DAW version another machine would need. Best effort."""
