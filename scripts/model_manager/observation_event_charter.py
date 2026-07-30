"""Charter-runner tick observation emitters (extracted from observation_event)."""

from __future__ import annotations

from scripts.model_manager.observation_event import _emit


async def emit_manage_charter_tick_started() -> None:
    """Charter-runner tick started with manage lifecycle."""
    await _emit("manage.charter.tick.started", {})


async def emit_manage_charter_tick_stopped() -> None:
    """Charter-runner tick stopped with manage lifecycle."""
    await _emit("manage.charter.tick.stopped", {})


async def emit_manage_charter_tick_wake(
    *,
    root: str,
    signal: str,
    coalesced_n: int,
) -> None:
    """WakeHub enqueued a root into the dirty set (M2 telemetry)."""
    await _emit(
        "manage.charter.tick.wake",
        {"root": root, "signal": signal, "coalesced_n": coalesced_n},
    )


async def emit_manage_charter_tick_scanned(
    *,
    roots: int,
    admitted: int,
    skipped_by_reason: dict[str, int] | None = None,
) -> None:
    """One scan pass over enrolled roots completed."""
    payload: dict = {"roots": roots, "admitted": admitted}
    if skipped_by_reason is not None:
        payload["skipped_by_reason"] = dict(skipped_by_reason)
    await _emit("manage.charter.tick.scanned", payload)


async def emit_manage_charter_tick_root_skipped(
    *,
    root: str,
    reason: str,
    checkpoint_turn: int | None = None,
    half: str | None = None,
    predicate_id: str | None = None,
    wip_snippet: str | None = None,
    fingerprint: str | None = None,
    row_rejections: list[dict[str, str]] | None = None,
    rows_considered: int | None = None,
) -> None:
    """Emit per-root ineligible Decision so silent starve is observable.

    ``wip_snippet`` is set when ``reason=wip_active`` so operators can see the
    rejected WIP body token without fetching the full CHECKPOINT (dogfood 5854).

    ``row_rejections`` lists per-row predicate failures for empty-hopper skips;
    ``rows_considered`` is the count of gated Next-pickup rows evaluated (0 when
    none were enumerated).
    """
    payload: dict = {
        "root": root,
        "reason": reason,
        "checkpoint_turn": checkpoint_turn,
    }
    if half is not None:
        payload["half"] = half
    if predicate_id is not None:
        payload["predicate_id"] = predicate_id
    if wip_snippet is not None:
        payload["wip_snippet"] = wip_snippet[:120]
    if fingerprint is not None:
        payload["fingerprint"] = fingerprint
    if row_rejections is not None:
        payload["row_rejections"] = row_rejections
    if rows_considered is not None:
        payload["rows_considered"] = rows_considered
    await _emit("manage.charter.tick.root_skipped", payload)


async def emit_manage_charter_tick_root_closed(
    *,
    root: str,
    reason: str,
    checkpoint_turn: int | None = None,
    closed: bool,
    unenrolled: bool,
) -> None:
    """Emit after a state-close attempt on an enrolled root."""
    await _emit(
        "manage.charter.tick.root_closed",
        {
            "root": root,
            "reason": reason,
            "checkpoint_turn": checkpoint_turn,
            "closed": closed,
            "unenrolled": unenrolled,
        },
    )


async def emit_manage_charter_tick_admitted(
    *,
    root: str,
    dispatch_id: str,
    worker_thread: str,
    objective: str | None = None,
) -> None:
    """A fresh windowed cursor-sdk continuation was admitted for a root."""
    payload: dict[str, str] = {
        "root": root,
        "dispatch_id": dispatch_id,
        "worker_thread": worker_thread,
    }
    if objective:
        payload["objective"] = objective
    await _emit(
        "manage.charter.tick.admitted",
        payload,
    )


async def emit_manage_charter_tick_window_failed(*, root: str, reason: str) -> None:
    """A window failed/stalled; root is stopped pending human re-arm."""
    await _emit("manage.charter.tick.window_failed", {"root": root, "reason": reason})


async def emit_manage_charter_tick_intent_healed(
    *,
    root: str,
    window_index: int,
    worker_thread: str = "",
) -> None:
    """Clear stale admit-intent after out-of-band completion."""
    await _emit(
        "manage.charter.tick.intent_healed",
        {
            "root": root,
            "window_index": window_index,
            "worker_thread": worker_thread,
        },
    )


async def emit_manage_charter_tick_self_healed(
    *,
    root: str,
    reason: str,
    window_index: int,
    worker_thread: str,
    heal_count: int = 0,
    harvested: bool = True,
) -> None:
    """Autonomous self-heal posted a recovery CHECKPOINT and reset CapStore stop."""
    await _emit(
        "manage.charter.tick.self_healed",
        {
            "root": root,
            "reason": reason,
            "window_index": window_index,
            "worker_thread": worker_thread,
            "heal_count": heal_count,
            "harvested": harvested,
        },
    )


async def emit_manage_charter_tick_self_heal_aborted(
    *,
    root: str,
    reason: str,
    window_index: int,
) -> None:
    """Self-heal declined after a partial check."""
    await _emit(
        "manage.charter.tick.self_heal_aborted",
        {
            "root": root,
            "reason": reason,
            "window_index": window_index,
        },
    )


async def emit_manage_charter_tick_consult_stall_recovered(
    *,
    root: str,
    reason: str,
    window_index: int,
    worker_thread: str,
    heal_count: int = 0,
    harvested: bool = True,
) -> None:
    """Consult-mode stall recovery posted CHECKPOINT."""
    await _emit(
        "manage.charter.tick.consult_stall_recovered",
        {
            "root": root,
            "reason": reason,
            "window_index": window_index,
            "worker_thread": worker_thread,
            "heal_count": heal_count,
            "harvested": harvested,
        },
    )


async def emit_manage_charter_tick_consult_harvested(
    *,
    root: str,
    window_index: int,
    consult_thread: str,
    verdict: str,
    consultant_family: str,
    consultant_substrate: str,
    consultant_model: str | None = None,
    cortex_mirror: str | None = None,
) -> None:
    """B8 consult provenance harvested — ``manage.charter.tick.consult.harvested``."""
    payload: dict = {
        "root": root,
        "window_index": window_index,
        "consult_thread": consult_thread,
        "verdict": verdict,
        "consultant_family": consultant_family,
        "consultant_substrate": consultant_substrate,
    }
    if consultant_model:
        payload["consultant_model"] = consultant_model
    if cortex_mirror:
        payload["cortex_mirror"] = cortex_mirror
    await _emit("manage.charter.tick.consult.harvested", payload)


async def emit_manage_charter_tick_harvest_rejected(
    *,
    root: str,
    window_index: int,
    field_path: str,
    checkpoint_subject: str | None = None,
) -> None:
    """Footer-invalid harvest reject — ``manage.charter.tick.harvest_rejected``.

    Parallel to ``root_skipped`` (admission half): makes the harvest-half
    fail-closed visible without reading per-root logs.
    """
    payload: dict = {
        "root": root,
        "window_index": window_index,
        "field_path": field_path,
    }
    if checkpoint_subject:
        payload["checkpoint_subject"] = checkpoint_subject[:120]
    await _emit("manage.charter.tick.harvest_rejected", payload)


async def emit_manage_charter_tick_harvest_footer_carveout(
    *,
    root: str,
    window_index: int,
    checkpoint_subject: str,
    carveout: str = "machine_authored",
) -> None:
    """Machine-CHECKPOINT footer-gate bypass — P3-AC3 observability instrument.

    ``reject_harvest_without_footer`` accepts self-heal / consult-stall subjects
    without the fail-closed footer check. Emit on that branch so a silent accept
    cannot vacate the AC3 bus audit (G3b R-admit C2).
    """
    await _emit(
        "manage.charter.tick.harvest_footer_carveout",
        {
            "root": root,
            "window_index": window_index,
            "checkpoint_subject": (checkpoint_subject or "")[:120],
            "carveout": carveout,
        },
    )


async def emit_manage_charter_tick_closed(
    *,
    root: str,
    window_index: int,
    worker_thread: str,
    checkpoint_turn: int,
    worker_closed: bool | None,
) -> None:
    """Window harvested after CHECKPOINT."""
    await _emit(
        "manage.charter.tick.closed",
        {
            "root": root,
            "window_index": window_index,
            "worker_thread": worker_thread,
            "checkpoint_turn": checkpoint_turn,
            "turn_number": checkpoint_turn,
            "worker_closed": worker_closed,
        },
    )


async def emit_manage_charter_implement_gate_bypassed(
    *,
    root: str,
    window_index: int,
    worker_thread: str,
    dispatch_id: str,
    source_ref: str,
    turn_number: int,
) -> None:
    """Harvest saw an ungated implement closeout."""
    await _emit(
        "manage.charter.implement_gate_bypassed",
        {
            "root": root,
            "window_index": window_index,
            "worker_thread": worker_thread,
            "dispatch_id": dispatch_id,
            "source_ref": source_ref,
            "turn_number": turn_number,
        },
    )


async def emit_manage_charter_tick_frictions_audit_passed(
    *,
    root: str,
    window_index: int,
    non_actionable_rate: float,
) -> None:
    await _emit(
        "manage.charter.tick.frictions_audit_passed",
        {
            "root": root,
            "window_index": window_index,
            "non_actionable_rate": round(non_actionable_rate, 4),
        },
    )


async def emit_manage_charter_tick_frictions_audit_failed(
    *,
    root: str,
    window_index: int,
    failure_class: str,
    non_actionable_rate: float,
) -> None:
    await _emit(
        "manage.charter.tick.frictions_audit_failed",
        {
            "root": root,
            "window_index": window_index,
            "failure_class": failure_class,
            "non_actionable_rate": round(non_actionable_rate, 4),
        },
    )


async def emit_manage_charter_tick_frictions_ceremonial_suspected(
    *,
    root: str,
    window_index: int,
    non_actionable_rate: float,
) -> None:
    await _emit(
        "manage.charter.tick.frictions_ceremonial_suspected",
        {
            "root": root,
            "window_index": window_index,
            "non_actionable_rate": round(non_actionable_rate, 4),
        },
    )


async def emit_manage_charter_tick_frictions_filed_uncited(
    *,
    root: str,
    window_index: int,
    uncited_ids: list[int],
) -> None:
    await _emit(
        "manage.charter.tick.frictions_filed_uncited",
        {
            "root": root,
            "window_index": window_index,
            "uncited_ids": uncited_ids,
        },
    )


async def emit_manage_charter_tick_frictions_audit_not_applicable(
    *,
    root: str,
    window_index: int,
    reason: str,
) -> None:
    await _emit(
        "manage.charter.tick.frictions_audit_not_applicable",
        {"root": root, "window_index": window_index, "reason": reason},
    )


async def emit_cortex_friction_todo_enqueued(
    *,
    assertion_id: int,
    todo_id: str,
    charter_root: str,
) -> None:
    await _emit(
        "cortex.friction.todo.enqueued",
        {
            "assertion_id": assertion_id,
            "todo_id": todo_id,
            "charter_root": charter_root,
        },
        source="cortex",
    )


async def emit_manage_charter_tick_waiting_open(*, root: str, age_s: int) -> None:
    await _emit("manage.charter.tick.waiting_open", {"root": root, "age_s": age_s})


async def emit_manage_charter_tick_error(*, reason: str) -> None:
    await _emit("manage.charter.tick.error", {"reason": reason})


async def emit_manage_charter_tick_reloaded(*, modules: list[str]) -> None:
    await _emit(
        "manage.charter.tick.reloaded",
        {"modules": modules, "count": len(modules)},
    )


async def emit_manage_charter_tick_paused(
    *,
    reason: str,
    set_by: str,
    set_at: float,
) -> None:
    """Operator armed the durable tick hold (survives manage quit/start)."""
    await _emit(
        "manage.charter.tick.paused",
        {"reason": reason, "set_by": set_by, "set_at": set_at},
    )


async def emit_manage_charter_tick_resumed(
    *,
    was_held: bool,
    reason: str | None = None,
) -> None:
    """Operator cleared the durable tick hold; next interval runs a normal tick."""
    payload: dict = {"was_held": was_held}
    if reason is not None:
        payload["reason"] = reason
    await _emit("manage.charter.tick.resumed", payload)


async def emit_manage_charter_caps_cleared(
    *, root: str, reason: str | None = None
) -> None:
    """CapStore stop cleared — wakes root for immediate re-admit check (§B3)."""
    payload: dict = {"root": root}
    if reason is not None:
        payload["reason"] = reason
    await _emit("manage.charter.caps.cleared", payload)


async def emit_manage_charter_tick_held(
    *,
    reason: str,
    set_by: str,
    set_at: float,
) -> None:
    """Rate-limited heartbeat while the tick loop skips passes under hold."""
    await _emit(
        "manage.charter.tick.held",
        {"reason": reason, "set_by": set_by, "set_at": set_at},
    )


async def emit_manage_charter_tick_propagation_started(
    *,
    root: str,
    window_index: int,
    services: list[str],
    charter_reload: bool,
    rows: list[dict] | None = None,
) -> None:
    await _emit(
        "manage.charter.tick.propagation_started",
        {
            "root": root,
            "window_index": window_index,
            "services": services,
            "charter_reload": charter_reload,
            "rows": rows or [],
        },
    )


async def emit_manage_charter_tick_propagation_completed(
    *,
    root: str,
    window_index: int,
    results: dict,
) -> None:
    await _emit(
        "manage.charter.tick.propagation_completed",
        {
            "root": root,
            "window_index": window_index,
            "status": results.get("status"),
            "services": results.get("services"),
            "charter_reload": results.get("charter_reload"),
            "skipped_lines": results.get("skipped_lines"),
            "closed": results.get("closed"),
            "remaining": results.get("remaining"),
            "escalated": results.get("escalated"),
            "scoreboard": results.get("scoreboard"),
        },
    )


async def emit_manage_charter_tick_propagation_escalated(
    *,
    root: str,
    window_index: int,
    escalated: list[dict],
) -> None:
    await _emit(
        "manage.charter.tick.propagation_escalated",
        {
            "root": root,
            "window_index": window_index,
            "escalated": escalated,
        },
    )


async def emit_manage_charter_root_blocked(
    *,
    root: str,
    reason: str,
    set_by: str,
    prior_status: str,
    unenrolled: bool,
    tip_class: str,
    wip_window_id: str | None,
) -> None:
    """Operator armed a durable per-root hold on the typed ledger."""
    await _emit(
        "manage.charter.root.blocked",
        {
            "root": root,
            "reason": reason,
            "set_by": set_by,
            "prior_status": prior_status,
            "unenrolled": unenrolled,
            "tip_class": tip_class,
            "wip_window_id": wip_window_id,
        },
    )


async def emit_manage_charter_root_unblocked(
    *,
    root: str,
    set_by: str,
    prior_status: str,
    reenrolled: bool,
) -> None:
    """Operator cleared a durable per-root hold; root returns to IDLE admits."""
    await _emit(
        "manage.charter.root.unblocked",
        {
            "root": root,
            "set_by": set_by,
            "prior_status": prior_status,
            "reenrolled": reenrolled,
        },
    )
