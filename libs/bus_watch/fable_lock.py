"""Single-Fable lock — operator invariant (2026-09-10): at most ONE Fable 5.1 seat
runs across Cursor IDE tabs and cursor-sdk dispatches.

The liaison tick loop claims the lock for its holder (``ide:<root>`` or
``sdk:<dispatch_id>``), refreshes it every poll, and releases it before a hop.
A holder whose heartbeat is older than ``LOCK_STALE_S`` is treated as dead
(crashed tab / killed dispatch). An attended ``ide:`` claim against a live
``sdk:`` holder records ``preempt_by``; the headless loop parks on its next poll.
The hop counter survives the release/claim seam so the per-night cap holds.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
WATCH_DIR = _REPO / "tmp" / "watchers"
FABLE_LOCK = WATCH_DIR / "liaison-fable.lock"
LOCK_STALE_S = 1800.0
MAX_HOPS_PER_NIGHT = 8


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_lock() -> dict[str, Any]:
    try:
        data = json.loads(FABLE_LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        data["age_s"] = round(time.time() - FABLE_LOCK.stat().st_mtime, 1)
    except OSError:
        data["age_s"] = None
    return data


def claim_fable_lock(holder: str, *, hop: bool) -> dict[str, Any]:
    """Claim the single-Fable lock for ``holder``; refuse while a live other holder exists."""
    current = read_lock()
    live = bool(current.get("holder")) and (current.get("age_s") or 0) < LOCK_STALE_S
    if live and current.get("holder") != holder:
        # Attended seat outranks a headless holder: record the request; the sdk
        # loop sees ``preempt_by`` on its next poll, parks, and releases.
        if holder.startswith("ide:") and str(current.get("holder")).startswith("sdk:"):
            current["preempt_by"] = holder
            FABLE_LOCK.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return {"ok": False, "reason": "held_preempt_requested", "lock": current}
        return {"ok": False, "reason": "held", "lock": current}
    hops = int(current.get("hops") or 0) + (1 if hop else 0)
    payload = {
        "holder": holder,
        "claimed_at": _utcnow(),
        "hops": hops,
        "born_at": current.get("born_at") or _utcnow(),
    }
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    FABLE_LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "lock": {**payload, "age_s": 0.0},
        "stale_broken": bool(current.get("holder")) and not live,
    }


def refresh_fable_lock(holder: str) -> bool:
    current = read_lock()
    if current.get("holder") != holder:
        return False
    os.utime(FABLE_LOCK, None)
    return True


def release_fable_lock(holder: str) -> dict[str, Any]:
    current = read_lock()
    if current and current.get("holder") not in (holder, None):
        return {"ok": False, "reason": "not_holder", "lock": current}
    # Keep the hop counter across the release/claim seam so the night cap survives hops.
    if current:
        FABLE_LOCK.write_text(
            json.dumps(
                {
                    "holder": None,
                    "released_at": _utcnow(),
                    "hops": current.get("hops", 0),
                    "born_at": current.get("born_at"),
                }
            ),
            encoding="utf-8",
        )
    return {"ok": True, "lock": read_lock()}


__all__ = [
    "FABLE_LOCK",
    "LOCK_STALE_S",
    "MAX_HOPS_PER_NIGHT",
    "WATCH_DIR",
    "claim_fable_lock",
    "read_lock",
    "refresh_fable_lock",
    "release_fable_lock",
]
