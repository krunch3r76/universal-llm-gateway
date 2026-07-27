"""Persist last-notified bus turn ids (per thread)."""

from __future__ import annotations

import json
import os
from pathlib import Path

_STATE_DIR = Path(
    os.environ.get(
        "PAGER_NOTIFY_STATE_DIR",
        str(Path.home() / ".local" / "share" / "pager-notify"),
    )
)
_STATE_FILE = _STATE_DIR / "bus_cursor.json"


def load_last_turns() -> dict[str, int]:
    if not _STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_last_turn(thread_id: str, turn_id: int) -> None:
    state = load_last_turns()
    prev = state.get(thread_id, 0)
    if turn_id <= prev:
        return
    state[thread_id] = turn_id
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
