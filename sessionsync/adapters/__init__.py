"""Adapter registry — one entry per DAW the app understands."""

from __future__ import annotations

from sessionsync.adapters.ableton.adapter import AbletonAdapter
from sessionsync.adapters.base import DAWAdapter

_ADAPTERS = {AbletonAdapter.format_name: AbletonAdapter}


def get_adapter(format_name: str) -> DAWAdapter:
    try:
        return _ADAPTERS[format_name]()
    except KeyError:
        raise ValueError(
            f"This repository holds a '{format_name}' project, which this version "
            "of SessionSync cannot open."
        ) from None


__all__ = ["AbletonAdapter", "DAWAdapter", "get_adapter"]
