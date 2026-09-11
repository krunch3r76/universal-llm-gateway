"""Single-Fable lock — operator invariant (2026-09-10): at most ONE Fable 5.1 seat
runs across Cursor IDE tabs and cursor-sdk dispatches.

The liaison tick loop holds a **ticker lease** (``ticker:<root>``), not the seat
mutex. Model seats claim ``ide:<root>`` or ``sdk:<dispatch_id>``, refresh the
declared lease on each ``--once`` tick, and release before a hop.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bus_watch.events import emit_night_id_reset

_REPO = Path(__file__).resolve().parents[2]
WATCH_DIR = _REPO / "tmp" / "watchers"
FABLE_LOCK = WATCH_DIR / "liaison-fable.lock"
TICKER_LOCK = WATCH_DIR / "liaison-ticker.lock"
LOCK_STALE_S = 1800.0
LEASE_SLACK_S = 1800
MAX_HOPS_PER_NIGHT = 8


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_night_id() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _lease_expires_at(
    claimed_at: str, max_hop_minutes: float, *, from_ts: float | None = None
) -> str:
    base = from_ts if from_ts is not None else (_parse_iso_ts(claimed_at) or time.time())
    expires = base + (max_hop_minutes * 60.0) + LEASE_SLACK_S
    return (
        datetime.fromtimestamp(expires, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _derived_expires_at(lock: dict[str, Any], max_hop_minutes: float = 60.0) -> float | None:
    raw = lock.get("expires_at")
    if raw:
        return _parse_iso_ts(str(raw))
    claimed = lock.get("claimed_at")
    if claimed:
        return _parse_iso_ts(_lease_expires_at(str(claimed), max_hop_minutes))
    try:
        return FABLE_LOCK.stat().st_mtime + LOCK_STALE_S
    except OSError:
        return None


def seat_lock_free(lock: dict[str, Any] | None = None, *, max_hop_minutes: float = 60.0) -> bool:
    """True when no live holder occupies the seat mutex."""
    data = lock if lock is not None else read_lock()
    holder = data.get("holder")
    if not holder:
        return True
    expires = _derived_expires_at(data, max_hop_minutes)
    if expires is None:
        return True
    return time.time() > expires


def _lock_age_s() -> float | None:
    try:
        return round(time.time() - FABLE_LOCK.stat().st_mtime, 1)
    except OSError:
        return None


def read_lock() -> dict[str, Any]:
    try:
        data = json.loads(FABLE_LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data["age_s"] = _lock_age_s()
    return data


def read_ticker_lock() -> dict[str, Any]:
    try:
        data = json.loads(TICKER_LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        data["age_s"] = round(time.time() - TICKER_LOCK.stat().st_mtime, 1)
    except OSError:
        data["age_s"] = None
    return data


def _hops_for_night(lock: dict[str, Any], night_id: str) -> int:
    by_night = lock.get("hops_by_night")
    if isinstance(by_night, dict) and night_id in by_night:
        return int(by_night.get(night_id) or 0)
    if lock.get("night_id") == night_id:
        return int(lock.get("hops") or 0)
    return 0


def _reconcile_night(
    lock: dict[str, Any], night_id: str, *, root_id: str = ""
) -> tuple[dict[str, Any], bool]:
    """Align lock counters to ``night_id``; emit when the night rolls."""
    previous = str(lock.get("night_id") or "")
    if previous and previous != night_id:
        emit_night_id_reset(
            root_id=root_id,
            previous_night_id=previous,
            new_night_id=night_id,
            previous_hops=_hops_for_night(lock, previous),
        )
        by_night = dict(lock.get("hops_by_night") or {})
        if previous:
            by_night[previous] = _hops_for_night(lock, previous)
        lock = {
            **lock,
            "night_id": night_id,
            "hops_by_night": by_night,
            "hops": 0,
        }
        return lock, True
    if not previous:
        lock = {**lock, "night_id": night_id}
    return lock, False


def claim_fable_lock(
    holder: str,
    *,
    hop: bool,
    max_hop_minutes: float = 60.0,
    night_id: str | None = None,
    root_id: str = "",
) -> dict[str, Any]:
    """Claim the single-Fable lock for ``holder``; refuse while a live other holder exists."""
    night = night_id or current_night_id()
    current = read_lock()
    current, _ = _reconcile_night(current, night, root_id=root_id)
    live = bool(current.get("holder")) and not seat_lock_free(current, max_hop_minutes=max_hop_minutes)
    if live and current.get("holder") != holder:
        if holder.startswith("ide:") and str(current.get("holder")).startswith("sdk:"):
            current["preempt_by"] = holder
            FABLE_LOCK.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return {"ok": False, "reason": "held_preempt_requested", "lock": current}
        return {"ok": False, "reason": "held", "lock": current}
    hops = _hops_for_night(current, night) + (1 if hop else 0)
    by_night = dict(current.get("hops_by_night") or {})
    by_night[night] = hops
    claimed_at = _utcnow()
    payload = {
        "holder": holder,
        "claimed_at": claimed_at,
        "expires_at": _lease_expires_at(claimed_at, max_hop_minutes),
        "hops": hops,
        "hops_by_night": by_night,
        "night_id": night,
        "tick_seq": int(current.get("tick_seq") or 0),
        "turns_seen": current.get("turns_seen"),
        "born_at": current.get("born_at") or claimed_at,
    }
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    FABLE_LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "lock": {**payload, "age_s": 0.0},
        "stale_broken": bool(current.get("holder")) and not live,
    }


def refresh_fable_lock(
    holder: str,
    *,
    max_hop_minutes: float = 60.0,
    turns_seen: int | None = None,
) -> bool:
    """Model-gated lease refresh — extends ``expires_at``, bumps ``tick_seq``."""
    current = read_lock()
    if current.get("holder") != holder:
        return False
    tick_seq = int(current.get("tick_seq") or 0) + 1
    now = _utcnow()
    payload = {
        **current,
        "claimed_at": current.get("claimed_at") or now,
        "expires_at": _lease_expires_at(now, max_hop_minutes, from_ts=time.time()),
        "tick_seq": tick_seq,
        "turns_seen": turns_seen if turns_seen is not None else current.get("turns_seen"),
    }
    payload.pop("age_s", None)
    FABLE_LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def release_fable_lock(holder: str) -> dict[str, Any]:
    current = read_lock()
    if current and current.get("holder") not in (holder, None):
        return {"ok": False, "reason": "not_holder", "lock": current}
    if current:
        FABLE_LOCK.write_text(
            json.dumps(
                {
                    "holder": None,
                    "released_at": _utcnow(),
                    "hops": current.get("hops", 0),
                    "hops_by_night": current.get("hops_by_night") or {},
                    "night_id": current.get("night_id"),
                    "born_at": current.get("born_at"),
                }
            ),
            encoding="utf-8",
        )
    return {"ok": True, "lock": read_lock()}


def claim_ticker_lease(root: str) -> dict[str, Any]:
    """Claim the plain-Python ticker lease for ``root``."""
    holder = f"ticker:{root}"
    current = read_ticker_lock()
    live = bool(current.get("holder")) and (current.get("age_s") or 0) < LOCK_STALE_S
    if live and current.get("holder") != holder:
        return {"ok": False, "reason": "held", "lock": current}
    payload = {"holder": holder, "claimed_at": _utcnow(), "root": root}
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    TICKER_LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "lock": {**payload, "age_s": 0.0}}


def refresh_ticker_lease(root: str) -> bool:
    current = read_ticker_lock()
    if current.get("holder") != f"ticker:{root}":
        return False
    payload = {**current, "refreshed_at": _utcnow()}
    payload.pop("age_s", None)
    TICKER_LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def release_ticker_lease(root: str) -> dict[str, Any]:
    current = read_ticker_lock()
    holder = f"ticker:{root}"
    if current and current.get("holder") not in (holder, None):
        return {"ok": False, "reason": "not_holder", "lock": current}
    if current:
        TICKER_LOCK.write_text(
            json.dumps({"holder": None, "released_at": _utcnow(), "root": root}),
            encoding="utf-8",
        )
    return {"ok": True, "lock": read_ticker_lock()}


__all__ = [
    "FABLE_LOCK",
    "LOCK_STALE_S",
    "MAX_HOPS_PER_NIGHT",
    "TICKER_LOCK",
    "WATCH_DIR",
    "claim_fable_lock",
    "claim_ticker_lease",
    "current_night_id",
    "read_lock",
    "read_ticker_lock",
    "refresh_fable_lock",
    "refresh_ticker_lease",
    "release_fable_lock",
    "release_ticker_lease",
    "seat_lock_free",
]
