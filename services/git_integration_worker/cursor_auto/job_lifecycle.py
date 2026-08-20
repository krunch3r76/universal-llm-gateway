"""Claim→admit→bind lifecycle phases for cursor-auto job observability.

``lifecycle_phase`` is the operator-facing phase enum (inv-16 / claimed-gate).
It is distinct from nested-SDK ``relay_phase`` (dispatched → sdk_terminal →
closeout_posted). Observer views are built only from persisted ledger fields
so MCP reads and enqueue lane counts share one source.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

PHASE_QUEUED = "queued"
PHASE_CLAIMED_PRE_ADMIT = "claimed_pre_admit"
PHASE_ADMITTED = "admitted"
PHASE_BOUND = "bound"
PHASE_TERMINAL_DONE = "terminal_done"
PHASE_TERMINAL_FAILED = "terminal_failed"
PHASE_TERMINAL_REPORT_UNDELIVERED = "terminal_report_undelivered"
PHASE_TERMINAL_SUPERSEDED = "terminal_superseded"

# Supersede-candidate phases: claimed through bound, before nested SDK finish.
SUPERSEDE_CANDIDATE_PHASES = frozenset(
    {PHASE_CLAIMED_PRE_ADMIT, PHASE_ADMITTED, PHASE_BOUND}
)

# Nested relay phases that mean "not a supersede candidate" (SDK finished).
_RELAY_PAST_NESTED = frozenset({"sdk_terminal", "closeout_posted"})


def terminal_phase_for_status(status: str) -> str:
    """Map a terminal job status onto a ``lifecycle_phase`` value."""
    if status == "done":
        return PHASE_TERMINAL_DONE
    if status == "failed":
        return PHASE_TERMINAL_FAILED
    if status == "report_undelivered":
        return PHASE_TERMINAL_REPORT_UNDELIVERED
    if status == "superseded":
        return PHASE_TERMINAL_SUPERSEDED
    raise ValueError(f"not a terminal status: {status}")


def derive_lifecycle_phase(
    *,
    status: str | None,
    lifecycle_phase: str | None,
    claimed_at: str | None,
    admitted_at: str | None,
    bound_at: str | None,
    dispatch_id: str | None,
) -> str:
    """Resolve observer phase, preferring the persisted column when present."""
    if lifecycle_phase:
        return str(lifecycle_phase)
    if status in ("done", "failed", "report_undelivered", "superseded"):
        return terminal_phase_for_status(str(status))
    if bound_at or dispatch_id:
        return PHASE_BOUND
    if admitted_at:
        return PHASE_ADMITTED
    if claimed_at or status == "claimed":
        return PHASE_CLAIMED_PRE_ADMIT
    return PHASE_QUEUED


def is_supersede_candidate_row(
    *,
    status: str | None,
    lifecycle_phase: str,
    relay_phase: str | None,
) -> bool:
    """True when the row counts toward ``same_thread_claimed`` / supersede."""
    if status != "claimed":
        return False
    if (relay_phase or "none") in _RELAY_PAST_NESTED:
        return False
    return lifecycle_phase in SUPERSEDE_CANDIDATE_PHASES


def observer_view_from_row(row: Any) -> dict[str, Any]:
    """Build the codeblind observer dict from a ledger SQLite row.

    Callers assert on this shape alone — not writer internals — to distinguish
    wedged-pre-admit, wedged-post-admit-pre-bind, and healthy answer+escalation.
    """
    keys = row.keys() if hasattr(row, "keys") else []
    status = row["status"] if "status" in keys else None
    claimed_at = row["claimed_at"] if "claimed_at" in keys else None
    admitted_at = row["admitted_at"] if "admitted_at" in keys else None
    bound_at = row["bound_at"] if "bound_at" in keys else None
    dispatch_id = row["dispatch_id"] if "dispatch_id" in keys else None
    raw_phase = row["lifecycle_phase"] if "lifecycle_phase" in keys else None
    relay_phase = row["relay_phase"] if "relay_phase" in keys else None
    phase = derive_lifecycle_phase(
        status=status,
        lifecycle_phase=raw_phase,
        claimed_at=claimed_at,
        admitted_at=admitted_at,
        bound_at=bound_at,
        dispatch_id=dispatch_id,
    )
    contract: str | None = None
    escalation: Any = None
    record_raw = row["record_json"] if "record_json" in keys else None
    if record_raw:
        try:
            data = json.loads(record_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            contract = data.get("contract")
            escalation = data.get("escalation")
    return {
        "job_id": row["job_id"] if "job_id" in keys else None,
        "thread_id": row["thread_id"] if "thread_id" in keys else None,
        "request_id": row["request_id"] if "request_id" in keys else None,
        "status": status,
        "lifecycle_phase": phase,
        "relay_phase": relay_phase or "none",
        "dispatch_id": dispatch_id,
        "enqueued_at": row["enqueued_at"] if "enqueued_at" in keys else None,
        "claimed_at": claimed_at,
        "admitted_at": admitted_at,
        "bound_at": bound_at,
        "ended_at": row["ended_at"] if "ended_at" in keys else None,
        "terminal_reason": (
            row["terminal_reason"] if "terminal_reason" in keys else None
        ),
        "contract": contract,
        "escalation": escalation,
        "turn_number": row["turn_number"] if "turn_number" in keys else None,
    }


def job_state_response(
    *,
    job_id: str | None,
    thread_id: str | None,
    view: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape the HTTP/MCP keyed job-state envelope from an observer view."""
    if view is None:
        return {
            "ok": True,
            "found": False,
            "job": None,
            "job_id": job_id,
            "thread_id": thread_id,
        }
    return {"ok": True, "found": True, "job": view}


def query_observer_state(
    conn: sqlite3.Connection,
    *,
    job_id: str | None = None,
    thread_id: str | None = None,
    include_terminal: bool = False,
) -> dict[str, Any] | None:
    """Load one observer view from an open ledger connection.

    Attaches waiter ``queue_position`` / ``queued_age_s`` (null when not queued).
    """
    if not job_id and not thread_id:
        return None
    if job_id:
        row = conn.execute(
            "SELECT * FROM cursor_auto_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
    elif include_terminal:
        row = conn.execute(
            "SELECT * FROM cursor_auto_jobs WHERE thread_id=? "
            "ORDER BY enqueued_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM cursor_auto_jobs WHERE thread_id=? "
            "AND status IN ('queued','claimed') "
            "ORDER BY enqueued_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        return None
    from services.git_integration_worker.cursor_auto.waiter_visibility import (
        waiter_fields_from_conn,
    )

    view = observer_view_from_row(row)
    view.update(waiter_fields_from_conn(conn, row["job_id"]))
    return view


def query_thread_lane_counts(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    exclude_job_id: str | None = None,
) -> dict[str, int]:
    """Count persisted same-thread pending/claimed peers (enqueue SoT)."""
    pending = 0
    claimed = 0
    rows = conn.execute(
        "SELECT * FROM cursor_auto_jobs WHERE thread_id=? "
        "AND status IN ('queued','claimed')",
        (thread_id,),
    ).fetchall()
    for row in rows:
        if exclude_job_id is not None and row["job_id"] == exclude_job_id:
            continue
        if row["status"] == "queued":
            pending += 1
            continue
        phase = derive_lifecycle_phase(
            status=row["status"],
            lifecycle_phase=row["lifecycle_phase"],
            claimed_at=row["claimed_at"],
            admitted_at=row["admitted_at"],
            bound_at=row["bound_at"],
            dispatch_id=row["dispatch_id"],
        )
        if is_supersede_candidate_row(
            status=row["status"],
            lifecycle_phase=phase,
            relay_phase=row["relay_phase"],
        ):
            claimed += 1
    return {
        "same_thread_pending": pending,
        "same_thread_claimed": claimed,
    }
