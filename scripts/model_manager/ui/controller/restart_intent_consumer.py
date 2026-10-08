"""Consumer-facing restart-intent projections — TTL ceiling semantics (7119 L5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_reason_codes import TERMINAL_STATUS_PROJECTION
from .restart_intent_states import _NEEDS_RECONCILE
from .restart_intent_store import Intent, intent_status_view

__all__ = [
    "DEADLINE_SEMANTICS",
    "STARGATE_DEADLINE_SEMANTICS",
    "deadline_semantics_for",
    "blocking_drain_result",
    "drain_deferred_result",
    "project_restart_intent_consumer",
    "project_restart_intent_last",
]

DEADLINE_SEMANTICS = (
    "Supervisor timeout ceiling — alert-only if drain has not converged by this "
    "instant. NOT the scheduled restart fire time; restarts proceed as soon as "
    "drain completes."
)

STARGATE_DEADLINE_SEMANTICS = (
    "Stargate idle-drain ceiling — self-preempt instant. Still busy at this "
    "instant moves force_requested then drained_restarting. NOT alert-only."
)


def deadline_semantics_for(service: str) -> str:
    if service == "stargate":
        return STARGATE_DEADLINE_SEMANTICS
    return DEADLINE_SEMANTICS


def project_restart_intent_consumer(
    intent: Intent,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One-call restart-intent read shape with reworded deadline semantics."""
    observed = now or datetime.now(UTC)
    core = intent_status_view(intent, now=observed)
    ceiling = core.get("deadline_at")
    return {
        **core,
        "service": intent.service,
        "action": intent.action,
        "reason": intent.reason,
        "deadline_ceiling_at": ceiling,
        "deadline_semantics": deadline_semantics_for(intent.service),
        "status_reason": intent.status_reason,
        "status_changed_at": intent.status_changed_at,
        "transitions": list(intent.transitions or []),
        "live": intent.status in _NEEDS_RECONCILE,
        # Legacy alias — same instant; semantics live in deadline_semantics.
        "deadline_at": ceiling,
        # Steer-restart: whether live cursor-sdk dispatches are parked at drain
        # start, and the last park sweep ({requested, refused, live_after, …}).
        "park_live": intent.park_live,
        "park_summary": intent.park_summary,
        "wait_for_boundary": intent.wait_for_boundary,
        "caller_agent": intent.caller_agent,
        "armed_at": intent.armed_at,
        "expires_at": intent.expires_at,
        "drain_begun": intent.drain_epoch is not None,
    }


def project_restart_intent_last(intent: Intent) -> dict[str, Any] | None:
    """Terminal row for ``busy_status.restart_intent_last``.

    ``status`` is the projected word (fired/failed/expired/cancelled), not the
    store status. ``reason`` is the latest status reason. ``None`` when this
    row is not a projected terminal.
    """
    projected = TERMINAL_STATUS_PROJECTION.get(intent.status)
    if projected is None:
        return None
    return {
        "intent_id": intent.intent_id,
        "status": projected,
        "reason": intent.status_reason or "",
        "armed_at": intent.armed_at,
        "terminal_at": intent.status_changed_at,
        "drain_begun": intent.drain_epoch is not None,
    }


def drain_deferred_result(
    intent: Intent,
    *,
    reason: str | None = None,
    activation_validation_id: str | None = None,
) -> dict[str, Any]:
    """The 202 envelope for a deferred, drain-supervised git-worker restart."""
    projected = project_restart_intent_consumer(intent)
    waiting = intent.wait_for_boundary and intent.drain_epoch is None
    state = "waiting_for_boundary" if waiting else "draining"
    default_reason = (
        "wait_for_boundary: GIW begin-drain will arm (admits open) until "
        "named-holder terminal or idle; epoch not yet posted (a:37197)"
        if waiting
        else (
            "draining or armed on GIW; completion via git_worker.drain events "
            "(wait_for_boundary keeps admits open while GIW reports armed=true)"
            if intent.wait_for_boundary
            else "draining; completion delivered via git_worker.drain events"
        )
    )
    result = {
        "status": "deferred",
        "state": state,
        "service": intent.service,
        "restart_intent_id": projected["restart_intent_id"],
        "deadline_ceiling_at": projected["deadline_ceiling_at"],
        "deadline_semantics": projected["deadline_semantics"],
        "deadline_at": projected["deadline_at"],
        "wait_for_boundary": intent.wait_for_boundary,
        "drain_begun": intent.drain_epoch is not None,
        "expires_at": intent.expires_at,
        "reason": reason or default_reason,
        "caller_must_exit_to_release_lease": True,
        "guidance": (
            "wait_for_boundary: GIW admits remain open until the armed drain "
            "flips (_draining). Query restart_intent_status / busy_status / "
            "drain-state.armed; do not poll-rearm on the legacy 600s window "
            "when expires_at is null."
            if intent.wait_for_boundary
            else (
                "If you hold the git_integration_worker write lease (cursor-sdk), "
                "exit this dispatch now — do not wait_healthy in-window. "
                "Activation proof is supervisor-owned; query via activation_validation_id "
                "or fleet_liveness(code_ref=…)."
            )
        ),
    }
    if activation_validation_id is not None:
        result["activation_validation_id"] = activation_validation_id
    return result


def blocking_drain_result(
    *, service: str, action: str, intent_id: str, final: Intent | None
) -> dict[str, Any]:
    """Terminal envelope for the fleet blocking path (ok vs error)."""
    drained_ok = final is not None and final.status in {
        "completed",
        "verifying_activation",
        "activation_unverified",
    }
    return {
        "status": "ok" if drained_ok else "error",
        "drain_status": (final.status if final is not None else "missing"),
        "service": service,
        "action": action,
        "restart_intent_id": intent_id,
    }
