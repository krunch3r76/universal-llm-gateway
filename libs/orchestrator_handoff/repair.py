"""Mechanized repair leg for house 10223 orchestrator heartbeat ticks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator_handoff.queue import HandoffQueue

_LAUNCHING_ORPHAN_S = 15 * 60
_LOCK_SOFT_STALE_S = 20 * 60
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


def _lock_timestamp(lock: dict[str, Any]) -> str | None:
    raw = lock.get("updated_at") or lock.get("acquired_at")
    return str(raw) if raw else None


def _reap_lock(
    queue: HandoffQueue,
    lock_path: Path,
    lock: dict[str, Any],
    *,
    reason: str,
    max_launch_attempts: int,
    repairs: list[dict[str, Any]],
) -> None:
    """Unlink lock; requeue linked item when queue_id is known."""
    lock_queue_id = str(lock.get("queue_id") or "")
    lock_holder = str(lock.get("holder") or "")
    result: dict[str, Any] | None = None
    if lock_queue_id:
        result = queue.requeue_launch_failure(
            lock_queue_id, reason, max_attempts=max_launch_attempts
        )
    lock_path.unlink(missing_ok=True)
    repairs.append(
        {
            "action": reason,
            "holder": lock_holder,
            "queue_id": lock_queue_id or None,
            "result": result,
        }
    )


def _repair_stale_locks(
    queue: HandoffQueue,
    lock_path: Path,
    *,
    max_launch_attempts: int,
    repairs: list[dict[str, Any]],
) -> None:
    lock = _read_lock(lock_path)
    if not lock:
        return

    updated_age = _age_s(_lock_timestamp(lock))
    if updated_age is None:
        _reap_lock(
            queue,
            lock_path,
            lock,
            reason="stale_lock_no_timestamp",
            max_launch_attempts=max_launch_attempts,
            repairs=repairs,
        )
        return

    if updated_age > _STALE_LOCK_S:
        _reap_lock(
            queue,
            lock_path,
            lock,
            reason="stale_lock",
            max_launch_attempts=max_launch_attempts,
            repairs=repairs,
        )
        return

    if updated_age > _LOCK_SOFT_STALE_S:
        reason = "stale_lock_acked" if lock.get("acked_at") else "launch_unacked"
        _reap_lock(
            queue,
            lock_path,
            lock,
            reason=reason,
            max_launch_attempts=max_launch_attempts,
            repairs=repairs,
        )


def _parse_ready_index_rows(index_path: Path) -> list[dict[str, str]]:
    if not index_path.is_file():
        return []
    rows: list[dict[str, str]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if "|" not in line or "**ready**" not in line.lower():
            continue
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) < 5:
            continue
        prompt_cell = parts[1].strip("` ")
        if not prompt_cell.endswith(".md"):
            continue
        rel = f"tmp/prompts/{prompt_cell}" if "/" not in prompt_cell else prompt_cell
        rows.append(
            {
                "work_prompt": rel,
                "priority": parts[3].strip() or "P2",
                "notes": parts[4].strip(),
                "intent": Path(prompt_cell).stem[:60],
            }
        )
    return rows


def _repair_regreenlit_ready(
    queue: HandoffQueue,
    index_path: Path | None,
    repairs: list[dict[str, Any]],
) -> None:
    """Re-enqueue index **ready** rows blocked only by terminal done items."""
    if index_path is None or not index_path.is_file():
        return
    active_prompts = {
        str(i.get("work_prompt") or "")
        for i in queue.list_items()
        if i.get("status") in ("queued", "launching", "in_flight")
    }
    for row in _parse_ready_index_rows(index_path):
        wp = row["work_prompt"]
        if not wp or wp in active_prompts:
            continue
        terminal = [
            i
            for i in queue.list_items()
            if i.get("work_prompt") == wp
            and i.get("status") in ("done", "cancelled", "failed")
        ]
        if not terminal:
            continue
        out = queue.enqueue(
            intent=row["intent"],
            work_prompt=wp,
            priority=row["priority"],
            notes=row["notes"],
        )
        if out.get("ok"):
            repairs.append(
                {
                    "action": "regreenlit_ready",
                    "work_prompt": wp,
                    "item_id": out["item"]["id"],
                }
            )
            active_prompts.add(wp)


def repair_tick(
    queue: HandoffQueue,
    *,
    lock_path: Path,
    index_path: Path | None = None,
    max_launch_attempts: int = _MAX_LAUNCH_ATTEMPTS,
) -> list[dict[str, Any]]:
    """Return repair actions taken (for heartbeat INFO trail)."""
    repairs: list[dict[str, Any]] = []

    _repair_stale_locks(
        queue, lock_path, max_launch_attempts=max_launch_attempts, repairs=repairs
    )

    lock = _read_lock(lock_path)
    lock_held = bool(lock)
    lock_queue_id = str(lock.get("queue_id") or "") if lock else ""

    for item in queue.list_items():
        item_id = str(item.get("id") or "")
        status = str(item.get("status") or "")
        started = item.get("started_at")

        if status == "launching":
            age = _age_s(str(started) if started else None)
            if age is not None and age > _LAUNCHING_ORPHAN_S:
                if lock_held and lock_queue_id == item_id:
                    lock_age = _age_s(_lock_timestamp(lock or {}))
                    if lock_age is None or lock_age > _LAUNCHING_ORPHAN_S:
                        _reap_lock(
                            queue,
                            lock_path,
                            lock or {},
                            reason="stale_launch_lock",
                            max_launch_attempts=max_launch_attempts,
                            repairs=repairs,
                        )
                        lock = _read_lock(lock_path)
                        lock_held = bool(lock)
                        lock_queue_id = str(lock.get("queue_id") or "") if lock else ""
                if not lock_held or lock_queue_id != item_id:
                    out = queue.requeue_launch_failure(
                        item_id, "orphan_launching", max_attempts=max_launch_attempts
                    )
                    repairs.append(
                        {
                            "action": "orphan_launching",
                            "item_id": item_id,
                            "result": out,
                        }
                    )

        if status == "in_flight":
            holder = str(item.get("holder") or "")
            if not lock_held or lock_queue_id != item_id:
                out = queue.requeue_launch_failure(
                    item_id, "orphan_in_flight", max_attempts=max_launch_attempts
                )
                repairs.append(
                    {
                        "action": "orphan_in_flight",
                        "item_id": item_id,
                        "holder": holder,
                        "result": out,
                    }
                )

    _repair_regreenlit_ready(queue, index_path, repairs)
    return repairs
