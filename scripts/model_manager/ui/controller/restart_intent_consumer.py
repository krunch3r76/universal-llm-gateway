"""Consumer-facing restart-intent projections — TTL ceiling semantics (7119 L5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_store import Intent, intent_status_view

__all__ = [
    "DEADLINE_SEMANTICS",
    "blocking_drain_result",
    "drain_deferred_result",
    "project_restart_intent_consumer",
]

DEADLINE_SEMANTICS = (
    "Supervisor timeout ceiling — alert-only if drain has not converged by this "
    "instant. NOT the scheduled restart fire time; restarts proceed as soon as "
    "drain completes."
)


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
        "deadline_semantics": DEADLINE_SEMANTICS,
        # Legacy alias — same instant; semantics live in deadline_semantics.
        "deadline_at": ceiling,
    }


def drain_deferred_result(
    intent: Intent,
    *,
    reason: str | None = None,
    activation_validation_id: str | None = None,
) -> dict[str, Any]:
    """The 202 envelope for a deferred, drain-supervised git-worker restart."""
    projected = project_restart_intent_consumer(intent)
    result = {
        "status": "deferred",
        "state": "draining",
        "service": intent.service,
        "restart_intent_id": projected["restart_intent_id"],
        "deadline_ceiling_at": projected["deadline_ceiling_at"],
        "deadline_semantics": projected["deadline_semantics"],
        "deadline_at": projected["deadline_at"],
        "reason": reason or "draining; completion delivered via git_worker.drain events",
        "caller_must_exit_to_release_lease": True,
        "guidance": (
            "If you hold the git_integration_worker write lease (cursor-sdk), "
            "exit this dispatch now — do not wait_healthy in-window. "
            "Activation proof is supervisor-owned; query via activation_validation_id "
            "or fleet_liveness(code_ref=…)."
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
