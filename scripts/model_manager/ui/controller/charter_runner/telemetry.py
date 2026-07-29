"""Charter-tick kernel telemetry — shadow/transition event facade."""

from __future__ import annotations

from typing import Any

from scripts.model_manager.observation_event import _emit


async def emit_tick_transition(
    *,
    root: str,
    from_status: str,
    to_status: str,
    transition: str,
    gid: str | None = None,
    pass_source: str | None = None,
) -> None:
    """Per-root transition applied (spec § Event Vocabulary)."""
    payload: dict[str, Any] = {
        "root": root,
        "from_status": from_status,
        "to_status": to_status,
        "transition": transition,
    }
    if gid:
        payload["gid"] = gid
    if pass_source:
        payload["pass_source"] = pass_source
    await _emit("manage.charter.tick.transition", payload)


async def emit_shadow_ledger_starved(
    *,
    reason: str,
    bus_roots: int,
) -> None:
    """Shadow path starved — ledger enrolled set empty at tick time."""
    await _emit(
        "manage.charter.tick.shadow.starved",
        {"reason": reason, "bus_roots": bus_roots},
    )


async def emit_shadow_diff(
    *,
    root: str,
    old_decision: str,
    kernel_transition: str,
    classification: str | None,
) -> None:
    """Shadow disagreement row (Phase 1 only)."""
    await _emit(
        "manage.charter.tick.shadow.diff",
        {
            "root": root,
            "old_decision": old_decision,
            "kernel_transition": kernel_transition,
            "classification": classification,
        },
    )


async def emit_admission_deferred_gate_held(
    *,
    root: str,
    holder_dispatch_id: str | None,
    holder_age_s: float | None,
    defer_count: int,
    queue_depth: int = 0,
) -> None:
    """G1 — observable defer when cursor_sdk_gate is held (not silent queue)."""
    payload: dict[str, Any] = {
        "root": root,
        "holder_dispatch_id": holder_dispatch_id,
        "defer_count": defer_count,
        "queue_depth": queue_depth,
    }
    if holder_age_s is not None:
        payload["holder_age_s"] = holder_age_s
    await _emit("manage.charter.tick.admission_deferred_gate_held", payload)


async def emit_admission_defer_escalated(
    *,
    root: str,
    reason: str,
    holder_dispatch_id: str | None,
    defer_count: int,
    holder_age_s: float | None = None,
) -> None:
    """G2/G3 — bounded defer age or orphan holder → needs-attended class."""
    payload: dict[str, Any] = {
        "root": root,
        "reason": reason,
        "holder_dispatch_id": holder_dispatch_id,
        "defer_count": defer_count,
    }
    if holder_age_s is not None:
        payload["holder_age_s"] = holder_age_s
    await _emit("manage.charter.tick.admission_defer_escalated", payload)


async def emit_tick_escalation(
    *,
    root: str,
    fire_attempt_outcome: str | None,
    fire_attempt_reason: str | None,
    worker_thread: str | None = None,
    refired: bool = False,
) -> None:
    """Episode open or TTL re-fire for unhealthy root (BIND 6249)."""
    payload: dict[str, Any] = {
        "root": root,
        "fire_attempt_outcome": fire_attempt_outcome,
        "fire_attempt_reason": fire_attempt_reason,
        "refired": refired,
    }
    if worker_thread:
        payload["worker_thread"] = worker_thread
    await _emit("manage.charter.tick.escalation", payload)


async def emit_root_skip_observed(
    *,
    root: str,
    skipped_reason: str | None,
    sos_reason: str | None,
    sticky_backing: str | None,
    holder_dispatch_id: str | None,
    skip_count: int,
    holder_age_s: float | None = None,
) -> None:
    """G1 analog — every root-skip observation is audible (a:26885)."""
    payload: dict[str, Any] = {
        "root": root,
        "skipped_reason": skipped_reason,
        "sos_reason": sos_reason,
        "sticky_backing": sticky_backing,
        "holder_dispatch_id": holder_dispatch_id,
        "skip_count": skip_count,
    }
    if holder_age_s is not None:
        payload["holder_age_s"] = holder_age_s
    await _emit("manage.charter.tick.root_skip_observed", payload)


async def emit_consult_queued(*, root: str, gid: str, role: str) -> None:
    await _emit(
        "manage.charter.tick.consult.queued",
        {"root": root, "gid": gid, "role": role},
    )


async def emit_consult_deferred(*, root: str, gid: str, next_retry: float) -> None:
    await _emit(
        "manage.charter.tick.consult.deferred",
        {"root": root, "gid": gid, "next_retry": next_retry},
    )


async def emit_enrollment_filtered(*, root: str, reason: str) -> None:
    """Old-tick path blocked for a ledger-migrated root (P2C-AC5 observability)."""
    await _emit(
        "manage.charter.tick.enrollment.filtered",
        {"root": root, "reason": reason},
    )


async def emit_storm_fuse_tripped(
    *,
    root: str,
    identity_key: str,
    category: str,
    tip_gid: str,
    mismatch_class: str,
    consecutive_count: int,
    held_friction_id: int,
) -> None:
    """Forbid §5 — conveyor hold + operator flag after N identical park frictions."""
    await _emit(
        "manage.charter.conveyor.storm_fuse_tripped",
        {
            "root": root,
            "identity_key": identity_key,
            "category": category,
            "tip_gid": tip_gid,
            "mismatch_class": mismatch_class,
            "consecutive_count": consecutive_count,
            "held_friction_id": held_friction_id,
            "fuse_threshold": 3,
        },
    )


__all__ = [
    "emit_admission_defer_escalated",
    "emit_admission_deferred_gate_held",
    "emit_consult_deferred",
    "emit_consult_queued",
    "emit_enrollment_filtered",
    "emit_root_skip_observed",
    "emit_shadow_diff",
    "emit_shadow_ledger_starved",
    "emit_storm_fuse_tripped",
    "emit_tick_escalation",
    "emit_tick_transition",
]
