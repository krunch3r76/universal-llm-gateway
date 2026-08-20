"""Waiter-visible FIFO position and queued-age for cursor-auto jobs.

Mirrors the SDK ``queue_position`` field: 1-indexed count of queued serial
peers at or ahead of self (``cursor_dispatch_ledger._queue_position_conn``).
Derived ``queued_age_s`` uses the ledger wall-clock ``enqueued_at`` ISO stamp
— not ``AutoJob.enqueued_at`` (monotonic) — so a waiter reads age without
subtracting clocks. Concurrent-class jobs are excluded, matching
``queue_admission_health`` admit-eligible pending.

Occupant-idle is not computed here. The waiter-starvation term is
``oldest_waiter_age_s`` / ``amber``. ``queue_admission_health`` folds
``amber`` into queue-not-serving ``red``; this module still never sets ``red``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from services.git_integration_worker.cursor_auto.execution_mode import (
    is_concurrent_execution_mode,
)

# Distinct from occupant-idle stall (90s). Healthy ~0.5s handoffs stay
# green; a 40-minute FIFO wait is unambiguously amber (and therefore
# queue-not-serving red at /liveness).
WAITER_STARVATION_AMBER_THRESHOLD_S = 120.0

_NULL_WAITER = {"queue_position": None, "queued_age_s": None}


def queued_age_s(
    enqueued_at_iso: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Wall-clock seconds since ledger ISO ``enqueued_at``, or None if unparsable."""
    if not enqueued_at_iso:
        return None
    try:
        then = datetime.fromisoformat(enqueued_at_iso)
    except ValueError:
        return None
    clock = now or (datetime.now(then.tzinfo) if then.tzinfo else datetime.now(UTC))
    return max(0.0, (clock - then).total_seconds())


def _execution_mode_from_record(record_json: str | None) -> str:
    try:
        data = json.loads(record_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return str(data.get("execution_mode") or "serial")


def _serial_queued_job_ids(rows: Sequence[sqlite3.Row]) -> list[str]:
    ordered: list[str] = []
    for row in rows:
        mode = _execution_mode_from_record(row["record_json"])
        if is_concurrent_execution_mode(mode):
            continue
        ordered.append(row["job_id"])
    return ordered


def _rounded_age(age: float | None) -> float | None:
    return round(age, 3) if age is not None else None


def waiter_fields_from_conn(
    conn: sqlite3.Connection,
    job_id: str,
) -> dict[str, Any]:
    """Return ``queue_position`` plus derived ``queued_age_s`` for one durable job."""
    row = conn.execute(
        "SELECT job_id, status, enqueued_at, record_json "
        "FROM cursor_auto_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None or row["status"] != "queued":
        return dict(_NULL_WAITER)
    if is_concurrent_execution_mode(_execution_mode_from_record(row["record_json"])):
        return dict(_NULL_WAITER)
    rows = conn.execute(
        "SELECT job_id, record_json FROM cursor_auto_jobs "
        "WHERE status='queued' ORDER BY enqueued_at ASC, rowid ASC"
    ).fetchall()
    ids = _serial_queued_job_ids(rows)
    try:
        position = ids.index(job_id) + 1
    except ValueError:
        return dict(_NULL_WAITER)
    return {
        "queue_position": position,
        "queued_age_s": _rounded_age(queued_age_s(row["enqueued_at"])),
    }


def waiter_starvation_from_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fleet waiter-starvation term — never mutates jobs, never sets ``red``.

    ``amber`` is the starvation bit. ``queue_admission_health`` may fold it
    into queue-not-serving ``red``; this function stays amber-only.
    """
    rows = conn.execute(
        "SELECT enqueued_at, record_json FROM cursor_auto_jobs "
        "WHERE status='queued' ORDER BY enqueued_at ASC, rowid ASC"
    ).fetchall()
    ages: list[float] = []
    for row in rows:
        if is_concurrent_execution_mode(
            _execution_mode_from_record(row["record_json"])
        ):
            continue
        age = queued_age_s(row["enqueued_at"])
        if age is not None:
            ages.append(age)
    oldest = max(ages) if ages else None
    return {
        "oldest_waiter_age_s": _rounded_age(oldest),
        "amber": oldest is not None and oldest > WAITER_STARVATION_AMBER_THRESHOLD_S,
        "amber_threshold_s": WAITER_STARVATION_AMBER_THRESHOLD_S,
    }


def waiter_fields_from_memory(
    order: Sequence[str],
    jobs: Mapping[str, Any],
    job_id: str,
    *,
    now_mono: float | None = None,
) -> dict[str, Any]:
    """In-memory FIFO receipt for ``durable=False`` test queues without a ledger."""
    job = jobs.get(job_id)
    if job is None or job.status != "queued":
        return dict(_NULL_WAITER)
    if is_concurrent_execution_mode(job.execution_mode):
        return dict(_NULL_WAITER)
    position = 0
    for jid in order:
        peer = jobs[jid]
        if peer.status != "queued":
            continue
        if is_concurrent_execution_mode(peer.execution_mode):
            continue
        position += 1
        if jid == job_id:
            clock = time.monotonic() if now_mono is None else now_mono
            return {
                "queue_position": position,
                "queued_age_s": round(max(0.0, clock - job.enqueued_at), 3),
            }
    return dict(_NULL_WAITER)


def waiter_starvation_from_memory(
    order: Sequence[str],
    jobs: Mapping[str, Any],
    *,
    now_mono: float | None = None,
) -> dict[str, Any]:
    """In-memory oldest-waiter projection for ``durable=False`` AutoJobQueue snapshots."""
    clock = time.monotonic() if now_mono is None else now_mono
    ages: list[float] = []
    for jid in order:
        job = jobs[jid]
        if job.status != "queued":
            continue
        if is_concurrent_execution_mode(job.execution_mode):
            continue
        ages.append(max(0.0, clock - job.enqueued_at))
    oldest = max(ages) if ages else None
    return {
        "oldest_waiter_age_s": _rounded_age(oldest),
        "amber": oldest is not None and oldest > WAITER_STARVATION_AMBER_THRESHOLD_S,
        "amber_threshold_s": WAITER_STARVATION_AMBER_THRESHOLD_S,
    }
