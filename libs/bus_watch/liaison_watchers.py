"""Completed watcher state files scoped to one continuity root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def watcher_belongs(data: dict[str, Any], path: Path, root_id: str) -> bool:
    """True when a watcher state file belongs to ``root_id`` (not merely fresh mtime)."""
    if data.get("root") == root_id:
        return True
    if data.get("thread") == root_id:
        return True
    prefix = f"{root_id}-"
    label = str(data.get("label") or "")
    return path.name.startswith(prefix) or label.startswith(prefix)


def collect_watchers(
    state: dict[str, Any],
    lane_ids: set[str],
    root_id: str,
    watch_dir: Path,
) -> list[dict[str, Any]]:
    """Completed watcher state files in scope for this root."""
    relayed = set(state.get("relayed_watchers") or [])
    born = float(state.get("born_epoch") or 0.0)
    out: list[dict[str, Any]] = []
    for path in sorted(watch_dir.glob("*.state.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        thread = str(data.get("thread") or "")
        in_scope = thread in lane_ids or (
            mtime >= born and watcher_belongs(data, path, root_id)
        )
        if data.get("status") == "complete" and path.name not in relayed and in_scope:
            out.append(
                {
                    "file": path.name,
                    "thread": data.get("thread"),
                    "label": data.get("label"),
                }
            )
    return out[:10]


__all__ = ["collect_watchers", "watcher_belongs"]
