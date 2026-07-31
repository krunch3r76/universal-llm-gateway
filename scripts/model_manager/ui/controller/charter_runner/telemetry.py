"""Charter-tick kernel telemetry — shadow/transition event facade."""

from __future__ import annotations

from typing import Any

from deploy_identity.code_version import resolve_code_version

from scripts.model_manager.observation_event import _emit


async def emit_tick_transition(
    *,
    root: str,
    from_status: str,
    to_status: str,
    transition: str,
    gid: str | None = None,
    pass_source: str | None = None,
    reason: str | None = None,
    code_version: str | None = None,
) -> None:
    """Per-root transition applied (spec § Event Vocabulary)."""
    payload: dict[str, Any] = {
        "root": root,
        "from_status": from_status,
        "to_status": to_status,
        "transition": transition,
        "code_version": code_version if code_version is not None else resolve_code_version(),
    }
    if gid:
        payload["gid"] = gid
    if pass_source:
        payload["pass_source"] = pass_source
    if reason:
        payload["reason"] = reason
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


async def emit_consult_drained(
    *,
    root: str,
    gid: str,
    role: str,
    queue_id: int,
    prior_status: str,
    reason: str,
) -> None:
    """Observation event when a consult_queue row is terminated on root close."""
    await _emit(
        "manage.charter.tick.consult.drained",
        {
            "root": root,
            "gid": gid,
            "role": role,
            "queue_id": queue_id,
            "prior_status": prior_status,
            "reason": reason,
        },
    )


async def emit_enrollment_filtered(*, root: str, reason: str) -> None:
    """Old-tick path blocked for a ledger-migrated root (P2C-AC5 observability)."""
    await _emit(
        "manage.charter.tick.enrollment.filtered",
        {"root": root, "reason": reason},
    )


async def emit_identical_work_refire_refused(
    *,
    root: str,
    work_key: str,
    work_key_version: str,
    source_ref: str | None,
    pickup_gid: str | None,
    consult_role: str | None,
    admission_mode: str,
    refuse_locus: str,
    holder_window_id: str | None,
    holder_dispatch_id: str | None,
    holder_thread_id: str | None,
    holder_age_s: float | None,
    carve_out: str | None,
    friction_id: int | None,
    probe_status: str,
) -> None:
    """Identical-work refire evaluation — refuse or audited carve-out bypass."""
    payload: dict[str, Any] = {
        "root": root,
        "work_key": work_key,
        "work_key_version": work_key_version,
        "admission_mode": admission_mode,
        "refuse_locus": refuse_locus,
        "probe_status": probe_status,
    }
    if source_ref:
        payload["source_ref"] = source_ref
    if pickup_gid:
        payload["pickup_gid"] = pickup_gid
    if consult_role:
        payload["consult_role"] = consult_role
    if holder_window_id:
        payload["holder_window_id"] = holder_window_id
    if holder_dispatch_id:
        payload["holder_dispatch_id"] = holder_dispatch_id
    if holder_thread_id:
        payload["holder_thread_id"] = holder_thread_id
    if holder_age_s is not None:
        payload["holder_age_s"] = holder_age_s
    if carve_out:
        payload["carve_out"] = carve_out
    if friction_id is not None:
        payload["friction_id"] = friction_id
    await _emit("manage.charter.conveyor.identical_work_refire_refused", payload)


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


async def emit_arc_lane_unset(*, root_id: str, todo_ref: str | None) -> None:
    """Warn when a codework todo lacks explicit ``arc_lane`` (defaults layer)."""
    await _emit(
        "manage.charter.tick.arc_lane.unset",
        {"root": root_id, "todo_ref": todo_ref or "", "arc_lane": "layer"},
    )


async def emit_birth_step(
    *,
    slug: str,
    root_id: str,
    step: str,
    outcome: str,
    detail: str = "",
) -> None:
    """Per-step birth ceremony observability (cold once-per-root signal)."""
    payload: dict[str, Any] = {
        "slug": slug,
        "root_id": root_id,
        "step": step,
        "outcome": outcome,
    }
    if detail:
        payload["detail"] = detail
    await _emit("manage.charter.birth.step", payload)


async def emit_birth_completed(
    *,
    slug: str,
    root_id: str,
    minted: bool,
    reclaimed: bool,
    seeded: bool,
    enrolled: bool,
    tip_posted: bool,
    duration_s: float,
) -> None:
    """Birth ceremony finished successfully for one charter root."""
    await _emit(
        "manage.charter.birth.completed",
        {
            "slug": slug,
            "root_id": root_id,
            "minted": minted,
            "reclaimed": reclaimed,
            "seeded": seeded,
            "enrolled": enrolled,
            "tip_posted": tip_posted,
            "duration_s": duration_s,
        },
    )


__all__ = [
    "emit_admission_defer_escalated",
    "emit_admission_deferred_gate_held",
    "emit_arc_lane_unset",
    "emit_birth_completed",
    "emit_birth_step",
    "emit_consult_deferred",
    "emit_consult_drained",
    "emit_consult_queued",
    "emit_enrollment_filtered",
    "emit_identical_work_refire_refused",
    "emit_root_skip_observed",
    "emit_shadow_diff",
    "emit_shadow_ledger_starved",
    "emit_storm_fuse_tripped",
    "emit_tick_escalation",
    "emit_tick_transition",
]
