"""Charter-runner tick observation emitters (extracted from observation_event)."""

from __future__ import annotations

from scripts.model_manager.observation_event import _emit


async def emit_manage_charter_tick_started() -> None:
    """Charter-runner tick started with manage lifecycle."""
    await _emit("manage.charter.tick.started", {})


async def emit_manage_charter_tick_stopped() -> None:
    """Charter-runner tick stopped with manage lifecycle."""
    await _emit("manage.charter.tick.stopped", {})


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
) -> None:
    """Emit per-root ineligible Decision so silent starve is observable.

    ``wip_snippet`` is set when ``reason=wip_active`` so operators can see the
    rejected WIP body token without fetching the full CHECKPOINT (dogfood 5854).
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
    *, root: str, dispatch_id: str, worker_thread: str
) -> None:
    """A fresh windowed cursor-sdk continuation was admitted for a root."""
    await _emit(
        "manage.charter.tick.admitted",
        {"root": root, "dispatch_id": dispatch_id, "worker_thread": worker_thread},
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


async def emit_manage_charter_tick_propagation_started(
    *,
    root: str,
    window_index: int,
    services: list[str],
    charter_reload: bool,
) -> None:
    await _emit(
        "manage.charter.tick.propagation_started",
        {
            "root": root,
            "window_index": window_index,
            "services": services,
            "charter_reload": charter_reload,
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
        },
    )
