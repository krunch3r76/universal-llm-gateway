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


__all__ = [
    "emit_consult_deferred",
    "emit_consult_queued",
    "emit_shadow_diff",
    "emit_shadow_ledger_starved",
    "emit_tick_transition",
]
