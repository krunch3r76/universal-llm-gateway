"""Persist last-notified bus turn ids (per thread)."""

from __future__ import annotations

import json
import os
import pwd
import time
from pathlib import Path


def _passwd_login_home() -> Path:
    """Login-directory home from passwd — immune to process ``HOME`` overlay leaks."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).expanduser()
    except (KeyError, OSError):
        return Path.home()


def state_dir() -> Path:
    """Resolve pager state directory lazily (honors ``PAGER_NOTIFY_STATE_DIR`` per call)."""
    override = os.environ.get("PAGER_NOTIFY_STATE_DIR")
    if override:
        return Path(override)
    return _passwd_login_home() / ".local" / "share" / "pager-notify"


def _state_file() -> Path:
    return state_dir() / "bus_cursor.json"


def _closeout_dedupe_file() -> Path:
    return state_dir() / "closeout_pager_dedupe.json"


def _tick_standing_file() -> Path:
    return state_dir() / "tick_standing_pager.json"


def load_last_turns() -> dict[str, int]:
    """Load per-thread last-notified turn ids from ``bus_cursor.json``."""
    path = _state_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
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
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_closeout_dedupe() -> dict[str, float]:
    path = _closeout_dedupe_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save_closeout_dedupe(state: dict[str, float]) -> None:
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    _closeout_dedupe_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


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


def _load_tick_standing() -> dict[str, str]:
    path = _tick_standing_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def claim_tick_standing_page(signature: str) -> bool:
    """Claim one SMS for a standing tick-skip signature until it changes.

    Standing stops (``blocked`` / ``stopped:*``) otherwise page every ~30s tick.
    Same *signature* → suppress; new signature → page once and remember.
    Key is fixed (``standing``); value is the last-paged signature string.
    """
    sig = str(signature or "").strip()
    if not sig:
        return False
    state = _load_tick_standing()
    if state.get("standing") == sig:
        return False
    state["standing"] = sig
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    _tick_standing_file().write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True
