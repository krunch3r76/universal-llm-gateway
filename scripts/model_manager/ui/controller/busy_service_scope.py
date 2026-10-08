"""Compact single-service projection of the ``busy_status`` read model.

``busy_status`` unscoped returns every service with its full ``active_work``
probe payload (~100–240KB when cursor-sdk lanes are loaded). A seat evaluating
the dispatch-kernel gate for one service only needs: is it busy, who holds it,
on which lane, and would a non-force restart defer. This module projects one
``busy_report`` entry into that answer **without** the ``active_work`` payload.

The status crosses an MCP boundary, so it is not a bare boolean: ``busy`` is
carried with ``as_of`` (when probed), ``source`` (which probe measured it),
``scope`` (which service), and ``epoch`` (the manage process identity plus the
live restart intent — the events that invalidate this reading). Friction a:36914.
"""

from __future__ import annotations

from typing import Any

BUSY_SOURCE = "restart_drain_gate.probe"


def project_service_busy(
    service: str,
    entry: dict[str, Any],
    *,
    as_of: str,
    manage_pid: int,
    manage_process_start_time: str,
) -> dict[str, Any]:
    """Return the scoped payload for one ``busy_report`` entry (``active_work`` omitted).

    ``entry`` is the per-service dict ``_busy_status`` already decorated with
    ``restart_intent`` / ``restart_window`` / ``active_work_summary``.
    """
    active_work = entry.get("active_work")
    work = active_work if isinstance(active_work, dict) else {}
    intent = entry.get("restart_intent")
    intent_id = intent.get("restart_intent_id") if isinstance(intent, dict) else None
    return {
        "service": service,
        "busy": bool(entry.get("busy")),
        "as_of": as_of,
        "source": BUSY_SOURCE,
        "scope": f"service:{service}",
        "epoch": {
            "manage_pid": manage_pid,
            "manage_process_start_time": manage_process_start_time,
            "restart_intent_id": intent_id,
        },
        "determination": entry.get("determination"),
        "restart_would_defer": bool(entry.get("restart_would_defer")),
        "holder": extract_holder(work),
        "lane": extract_lane(work),
        "probe_error": work.get("error"),
        "restart_intent": intent,
        "restart_intent_last": entry.get("restart_intent_last"),
        "restart_window": entry.get("restart_window"),
        "active_work_summary": entry.get("active_work_summary", ""),
    }


def extract_holder(work: dict[str, Any]) -> dict[str, Any] | None:
    """Structured holder identity, same preference order as ``busy_work_summary``.

    ``write_lease`` (the durable lane-A lease) wins; then the cursor-sdk gate's
    discriminated ``active_holder``; then the first ``active_ops`` row. ``None``
    when nothing names a holder — callers read ``busy`` / ``determination`` for
    the idle-vs-anonymous-busy distinction.
    """
    lease = work.get("write_lease")
    if isinstance(lease, dict) and lease.get("holder_dispatch_id"):
        return _holder_from_lease_row(lease, source="write_lease")
    gate_busy = _gate_busy_status(work)
    active_holder = gate_busy.get("active_holder")
    if isinstance(active_holder, dict) and active_holder.get("dispatch_id"):
        holders = gate_busy.get("active_holders")
        return {
            "dispatch_id": active_holder.get("dispatch_id"),
            "thread_id": active_holder.get("thread_id"),
            "model": active_holder.get("model"),
            "subject_preview": active_holder.get("subject_preview"),
            "status": active_holder.get("status"),
            "active_holder_count": len(holders) if isinstance(holders, list) else None,
            "source": "cursor_sdk_gate.busy_status",
        }
    ops = work.get("active_ops")
    if isinstance(ops, list) and ops and isinstance(ops[0], dict):
        op = ops[0]
        return {
            "dispatch_id": op.get("op_id"),
            "thread_id": op.get("thread_id"),
            "model": op.get("resolved_model") or op.get("model"),
            "subject_preview": op.get("subject_preview"),
            "status": op.get("state"),
            "kind": op.get("kind"),
            "active_holder_count": len(ops),
            "source": "active_ops",
        }
    return None


def _holder_from_lease_row(lease: dict[str, Any], *, source: str) -> dict[str, Any]:
    holders = lease.get("active_holders")
    return {
        "dispatch_id": lease.get("holder_dispatch_id"),
        "thread_id": lease.get("holder_thread_id"),
        "model": lease.get("holder_resolved_model"),
        "subject_preview": lease.get("holder_subject_preview"),
        "status": lease.get("holder_status"),
        "active_holder_count": len(holders) if isinstance(holders, list) else None,
        "source": source,
    }


def extract_lane(work: dict[str, Any]) -> dict[str, Any] | None:
    """Lane occupancy from the cursor-sdk gate; ``None`` for services without lanes.

    ``holder_lane`` is derived, not observed: the holder rows do not carry a
    lane, so it is named only when exactly one lane has active occupants
    (``holder_lane_basis=sole_active_lane``); otherwise ``None`` with basis
    ``mixed`` or ``none_active``.
    """
    gate = work.get("cursor_sdk_gate")
    if not isinstance(gate, dict):
        return None
    active_by_lane = gate.get("active_by_lane")
    occupied = (
        sorted(k for k, v in active_by_lane.items() if isinstance(v, int) and v > 0)
        if isinstance(active_by_lane, dict)
        else []
    )
    if len(occupied) == 1:
        holder_lane, basis = occupied[0], "sole_active_lane"
    elif occupied:
        holder_lane, basis = None, "mixed"
    else:
        holder_lane, basis = None, "none_active"
    return {
        "holder_lane": holder_lane,
        "holder_lane_basis": basis,
        "active_by_lane": active_by_lane,
        "queued_by_lane": gate.get("queued_by_lane"),
        "capacity_by_lane": _gate_busy_status(work).get("capacity_by_lane"),
        "capacity_disposition": gate.get("capacity_disposition"),
    }


def _gate_busy_status(work: dict[str, Any]) -> dict[str, Any]:
    gate = work.get("cursor_sdk_gate")
    if not isinstance(gate, dict):
        return {}
    busy = gate.get("busy_status")
    return busy if isinstance(busy, dict) else {}
