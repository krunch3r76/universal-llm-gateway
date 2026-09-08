"""Mechanized repair leg for house 10223 orchestrator heartbeat ticks."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator_handoff.queue import HandoffQueue

_LAUNCHING_ORPHAN_S = 15 * 60
_STALE_LOCK_S = 4 * 3600
_MAX_LAUNCH_ATTEMPTS = 3


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _age_s(value: str | None) -> float | None:
    t0 = _parse_ts(value)
    if not t0:
        return None
    return (datetime.now(UTC) - t0).total_seconds()


def _read_lock(lock_path: Path) -> dict[str, Any] | None:
    if not lock_path.is_file():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def repair_tick(
    queue: HandoffQueue,
    *,
    lock_path: Path,
    max_launch_attempts: int = _MAX_LAUNCH_ATTEMPTS,
) -> list[dict[str, Any]]:
    """Return repair actions taken (for heartbeat INFO trail)."""
    repairs: list[dict[str, Any]] = []
    lock = _read_lock(lock_path)
    lock_held = bool(lock)
    lock_queue_id = str(lock.get("queue_id") or "") if lock else ""
    lock_holder = str(lock.get("holder") or "") if lock else ""

    for item in queue.list_items():
        item_id = str(item.get("id") or "")
        status = str(item.get("status") or "")
        started = item.get("started_at")

        if status == "launching":
            age = _age_s(str(started) if started else None)
            if age is not None and age > _LAUNCHING_ORPHAN_S:
                if not lock_held or lock_queue_id != item_id:
                    out = queue.requeue_launch_failure(item_id, "orphan_launching", max_attempts=max_launch_attempts)
                    repairs.append({"action": "orphan_launching", "item_id": item_id, "result": out})

        if status == "in_flight":
            holder = str(item.get("holder") or "")
            if not lock_held or lock_queue_id != item_id:
                out = queue.requeue_launch_failure(item_id, "orphan_in_flight", max_attempts=max_launch_attempts)
                repairs.append({"action": "orphan_in_flight", "item_id": item_id, "holder": holder, "result": out})

    if lock:
        updated_age = _age_s(str(lock.get("updated_at") or lock.get("acquired_at")))
        if updated_age is not None and updated_age > _STALE_LOCK_S:
            if lock_queue_id:
                queue.requeue_launch_failure(
                    lock_queue_id, "stale_lock", max_attempts=max_launch_attempts
                )
            lock_path.unlink(missing_ok=True)
            repairs.append(
                {
                    "action": "stale_lock",
                    "holder": lock_holder,
                    "queue_id": lock_queue_id or None,
                }
            )
        elif lock_held and not lock.get("acked_at"):
            if updated_age is not None and updated_age > 20 * 60:
                if lock_queue_id:
                    out = queue.requeue_launch_failure(
                        lock_queue_id, "launch_unacked", max_attempts=max_launch_attempts
                    )
                    lock_path.unlink(missing_ok=True)
                    repairs.append(
                        {"action": "launch_unacked", "item_id": lock_queue_id, "result": out}
                    )

    return repairs
