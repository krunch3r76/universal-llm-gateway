"""Atomic watcher state + path helpers under tmp/watchers/."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_WATCHER_DIR = _REPO / "tmp" / "watchers"


@dataclass(frozen=True)
class WatcherPaths:
    label: str
    directory: Path
    state_file: Path
    pid_file: Path
    log_file: Path


def paths_for(label: str, *, directory: Path | None = None) -> WatcherPaths:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label.strip())
    if not safe:
        raise ValueError("watcher label must be non-empty")
    root = directory or DEFAULT_WATCHER_DIR
    root.mkdir(parents=True, exist_ok=True)
    return WatcherPaths(
        label=safe,
        directory=root,
        state_file=root / f"{safe}.state.json",
        pid_file=root / f"{safe}.pid",
        log_file=root / f"{safe}.log",
    )


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_state(path: Path, **fields: Any) -> dict[str, Any]:
    """Merge fields into state.json (atomic replace). Always stamps updated_at."""
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (OSError, json.JSONDecodeError):
            current = {}
    current.update(fields)
    current["updated_at"] = _utcnow()
    if "pid" not in current:
        current["pid"] = os.getpid()
    durable_write_text(path, json.dumps(current, indent=2, sort_keys=True) + "\n")
    return current


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
