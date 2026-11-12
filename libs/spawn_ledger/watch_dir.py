"""Neutral watcher directory path for spawn/house ledger JSONL (no bus_watch / GUI imports)."""

from __future__ import annotations

from pathlib import Path

from ulg_paths.repo_root import ulg_repo_root


def repo_root() -> Path:
    """ULG checkout root — same authority as ``bus_watch.fable_lock`` (see ``ulg_paths``)."""
    return ulg_repo_root(libs_anchor=Path(__file__))


WATCH_DIR = repo_root() / "tmp" / "watchers"

__all__ = ["WATCH_DIR", "repo_root"]
