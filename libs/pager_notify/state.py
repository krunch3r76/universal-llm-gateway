"""Persist last-notified bus turn ids (per thread)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_STATE_DIR = Path(
    os.environ.get(
        "PAGER_NOTIFY_STATE_DIR",
        str(Path.home() / ".local" / "share" / "pager-notify"),
    )
)
_STATE_FILE = _STATE_DIR / "bus_cursor.json"
_CLOSEOUT_DEDUPE_FILE = _STATE_DIR / "closeout_pager_dedupe.json"


def load_last_turns() -> dict[str, int]:
    """Load per-thread last-notified turn ids from ``bus_cursor.json``."""
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
    """Persist *turn_id* as the last notified turn for *thread_id* when newer."""
    state = load_last_turns()
    prev = state.get(thread_id, 0)
    if turn_id <= prev:
        return
    state[thread_id] = turn_id
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_closeout_dedupe() -> dict[str, float]:
    if not _CLOSEOUT_DEDUPE_FILE.is_file():
        return {}
    try:
        raw = json.loads(_CLOSEOUT_DEDUPE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save_closeout_dedupe(state: dict[str, float]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CLOSEOUT_DEDUPE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def claim_closeout_page(
    thread_id: str,
    status: str,
    *,
    now: float | None = None,
    ttl_s: float = 300.0,
) -> bool:
    """Claim a closeout SMS page for ``thread_id:status`` within *ttl_s*.

    Key is ``f"{thread_id}:{status.strip().lower()}"``. Returns ``False`` when
    the same key was paged within *ttl_s* wall-clock seconds (SMS suppressed).
    On claim, persists the epoch to ``closeout_pager_dedupe.json`` under
    ``PAGER_NOTIFY_STATE_DIR`` and returns ``True``.
    """
    epoch = time.time() if now is None else now
    key = f"{thread_id}:{str(status).strip().lower()}"
    state = _load_closeout_dedupe()
    prior = state.get(key)
    if prior is not None and (epoch - prior) < ttl_s:
        return False
    state[key] = epoch
    _save_closeout_dedupe(state)
    return True
