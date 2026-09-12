"""One liaison seat mutex per continuity root (``liaison-fable-<root>.lock``).

Gear-3 ticker holds ``ticker:<root>`` separately; seat holders are ``ide:`` or ``sdk:`` tab identities.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bus_watch.events import emit_night_id_reset

_REPO = Path(__file__).resolve().parents[2]
WATCH_DIR = _REPO / "tmp" / "watchers"
FABLE_LOCK = WATCH_DIR / "liaison-fable.lock"  # legacy constant; never read or written
TICKER_LOCK = WATCH_DIR / "liaison-ticker.lock"  # legacy; live ticker uses ticker_lock_path
LOCK_STALE_S = 1800.0
LEASE_SLACK_S = 1800
MAX_HOPS_PER_NIGHT = 8


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_root_id(root_id: str) -> str:
    rid = str(root_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        raise ValueError(f"invalid root_id: {root_id!r}")
    return rid


def fable_lock_path(root_id: str) -> Path:
    return WATCH_DIR / f"liaison-fable-{_validate_root_id(root_id)}.lock"


def ticker_lock_path(root_id: str) -> Path:
    return WATCH_DIR / f"liaison-ticker-{_validate_root_id(root_id)}.lock"


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
    base = (
        from_ts if from_ts is not None else (_parse_iso_ts(claimed_at) or time.time())
    )
    expires = base + (max_hop_minutes * 60.0) + LEASE_SLACK_S
    return (
        datetime.fromtimestamp(expires, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _derived_expires_at(
    lock: dict[str, Any],
    max_hop_minutes: float = 60.0,
    *,
    lock_path: Path | None = None,
) -> float | None:
    raw = lock.get("expires_at")
    if raw:
        return _parse_iso_ts(str(raw))
    claimed = lock.get("claimed_at")
    if claimed:
        return _parse_iso_ts(_lease_expires_at(str(claimed), max_hop_minutes))
    path = lock_path
    if path is None and lock.get("root"):
        path = fable_lock_path(str(lock["root"]))
    if path is None:
        return None
    try:
        return path.stat().st_mtime + LOCK_STALE_S
    except OSError:
        return None


def seat_lock_free(
    lock: dict[str, Any] | None = None,
    *,
    max_hop_minutes: float = 60.0,
    root_id: str = "",
) -> bool:
    """True when no live holder occupies the seat mutex."""
    data = lock if lock is not None else read_lock(root_id)
    holder = data.get("holder")
    if not holder:
        return True
    path = fable_lock_path(root_id) if root_id else None
    expires = _derived_expires_at(data, max_hop_minutes, lock_path=path)
    if expires is None:
        return True
    return time.time() > expires


def read_lock(root_id: str) -> dict[str, Any]:
    path = fable_lock_path(root_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["age_s"] = round(time.time() - path.stat().st_mtime, 1)
    except (OSError, ValueError):
        return {}
    return data


def read_ticker_lock(root: str) -> dict[str, Any]:
    path = ticker_lock_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["age_s"] = round(time.time() - path.stat().st_mtime, 1)
    except (OSError, ValueError):
        return {}
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
    take_over: bool = False,
) -> dict[str, Any]:
    """Claim seat for ``holder``; ``ide:`` preempts ``sdk:``; ``take_over`` preempts live ``ide:``."""
    rid = _validate_root_id(root_id)
    path = fable_lock_path(rid)
    night = night_id or current_night_id()
    current = read_lock(rid)
    current, _ = _reconcile_night(current, night, root_id=rid)
    live = bool(current.get("holder")) and not seat_lock_free(
        current, max_hop_minutes=max_hop_minutes, root_id=rid
    )
    if live and current.get("holder") != holder:
        live_holder = str(current.get("holder"))
        attended = holder.startswith("ide:")
        may_preempt = attended and (live_holder.startswith("sdk:") or take_over)
        if may_preempt:
            current["preempt_by"] = holder
            path.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return {"ok": False, "reason": "held_preempt_requested", "lock": current}
        return {"ok": False, "reason": "held", "lock": current}
    hops = _hops_for_night(current, night) + (1 if hop else 0)
    by_night = dict(current.get("hops_by_night") or {})
    by_night[night] = hops
    claimed_at = _utcnow()
    payload = {
        "holder": holder,
        "holder_pid": os.getpid(),
        "claimed_at": claimed_at,
        "expires_at": _lease_expires_at(claimed_at, max_hop_minutes),
        "hops": hops,
        "hops_by_night": by_night,
        "night_id": night,
        "tick_seq": int(current.get("tick_seq") or 0),
        "turns_seen": current.get("turns_seen"),
        "born_at": current.get("born_at") or claimed_at,
        "root": rid,
    }
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "lock": {**payload, "age_s": 0.0},
        "stale_broken": bool(current.get("holder")) and not live,
    }


def refresh_fable_lock(
    holder: str,
    *,
    root_id: str = "",
    max_hop_minutes: float = 60.0,
    turns_seen: int | None = None,
) -> bool:
    """Model-gated lease refresh — extends ``expires_at``, bumps ``tick_seq``."""
    rid = _validate_root_id(root_id)
    path = fable_lock_path(rid)
    current = read_lock(rid)
    if current.get("holder") != holder:
        return False
    tick_seq = int(current.get("tick_seq") or 0) + 1
    now = _utcnow()
    payload = {
        **current,
        "claimed_at": current.get("claimed_at") or now,
        "expires_at": _lease_expires_at(now, max_hop_minutes, from_ts=time.time()),
        "tick_seq": tick_seq,
        "turns_seen": turns_seen
        if turns_seen is not None
        else current.get("turns_seen"),
        "root": rid,
    }
    payload.pop("age_s", None)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def release_fable_lock(
    holder: str,
    *,
    root_id: str = "",
    pid: int | None = -1,
) -> dict[str, Any]:
    """Release seat; ``pid=-1`` is this process, ``pid=None`` is operator ``--release``."""
    rid = _validate_root_id(root_id)
    path = fable_lock_path(rid)
    current = read_lock(rid)
    if current and current.get("holder") not in (holder, None):
        return {"ok": False, "reason": "not_holder", "lock": current}
    caller = os.getpid() if pid == -1 else pid
    lock_pid = current.get("holder_pid") if current else None
    if caller is not None and lock_pid is not None and int(lock_pid) != int(caller):
        return {"ok": False, "reason": "not_holder_process", "lock": current}
    if current:
        path.write_text(
            json.dumps(
                {
                    "holder": None,
                    "released_at": _utcnow(),
                    "hops": current.get("hops", 0),
                    "hops_by_night": current.get("hops_by_night") or {},
                    "night_id": current.get("night_id"),
                    "born_at": current.get("born_at"),
                    "root": rid,
                }
            ),
            encoding="utf-8",
        )
    return {"ok": True, "lock": read_lock(rid)}


def claim_ticker_lease(root: str) -> dict[str, Any]:
    holder = f"ticker:{root}"
    path = ticker_lock_path(root)
    current = read_ticker_lock(root)
    live = bool(current.get("holder")) and (current.get("age_s") or 0) < LOCK_STALE_S
    if live and current.get("holder") != holder:
        return {"ok": False, "reason": "held", "lock": current}
    payload = {"holder": holder, "claimed_at": _utcnow(), "root": root}
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "lock": {**payload, "age_s": 0.0}}


def refresh_ticker_lease(root: str) -> bool:
    current = read_ticker_lock(root)
    if current.get("holder") != f"ticker:{root}":
        return False
    payload = {**current, "refreshed_at": _utcnow()}
    payload.pop("age_s", None)
    ticker_lock_path(root).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def release_ticker_lease(root: str) -> dict[str, Any]:
    current = read_ticker_lock(root)
    holder = f"ticker:{root}"
    if current and current.get("holder") not in (holder, None):
        return {"ok": False, "reason": "not_holder", "lock": current}
    if current:
        ticker_lock_path(root).write_text(
            json.dumps({"holder": None, "released_at": _utcnow(), "root": root}),
            encoding="utf-8",
        )
    return {"ok": True, "lock": read_ticker_lock(root)}


__all__ = [
    "FABLE_LOCK",
    "LOCK_STALE_S",
    "MAX_HOPS_PER_NIGHT",
    "TICKER_LOCK",
    "WATCH_DIR",
    "claim_fable_lock",
    "claim_ticker_lease",
    "current_night_id",
    "fable_lock_path",
    "read_lock",
    "read_ticker_lock",
    "refresh_fable_lock",
    "refresh_ticker_lease",
    "release_fable_lock",
    "release_ticker_lease",
    "seat_lock_free",
    "ticker_lock_path",
]
