"""Neutral watcher directory path for spawn/house ledger JSONL (no bus_watch / GUI imports)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """ULG checkout root — honors container ``PROJECT_ROOT=/data/project`` mounts."""
    project_root = Path(os.environ.get("PROJECT_ROOT", "")).expanduser()
    if project_root.is_dir():
        if (project_root / "libs" / "spawn_ledger").is_dir():
            return project_root
        nested = project_root / "universal-llm-gateway"
        if nested.is_dir() and (nested / "libs" / "spawn_ledger").is_dir():
            return nested
    return Path(__file__).resolve().parents[2]


WATCH_DIR = repo_root() / "tmp" / "watchers"

__all__ = ["WATCH_DIR", "repo_root"]
