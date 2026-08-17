"""Failures worded the way they will be shown to a musician, not a developer."""

from __future__ import annotations


class SessionSyncError(Exception):
    """Base class for everything this app raises on purpose."""


class NotAProjectError(SessionSyncError):
    """The chosen folder is not a project this app can sync."""


class StalePushError(SessionSyncError):
    """Someone else pushed since this copy was last up to date."""

    def __init__(self, local_version: int, remote_version: int):
        self.local_version = local_version
        self.remote_version = remote_version
        super().__init__(
            f"This project is now version {remote_version}, but you started from "
            f"version {local_version}. Pull latest before pushing — your own work "
            "stays where it is."
        )


class NothingToPushError(SessionSyncError):
    """No files changed, so there is nothing to record."""


class LocalChangesWouldBeReplacedError(SessionSyncError):
    """Pulling would replace files this Mac has edited but not pushed."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        listed = "\n  ".join(paths)
        super().__init__(
            "The new version changes files you have edited but not pushed:\n  "
            f"{listed}\n"
            "Pull would replace your versions of these files."
        )


class ProjectBehindError(SessionSyncError):
    """Refusing to start a session from an out-of-date copy."""

    def __init__(self, local_version: int, remote_version: int):
        self.local_version = local_version
        self.remote_version = remote_version
        super().__init__(
            f"This project is now version {remote_version}, you have version "
            f"{local_version}. Pull latest before you start working."
        )
