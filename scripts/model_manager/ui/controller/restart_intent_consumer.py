"""Consumer-facing restart-intent projections — TTL ceiling semantics (7119 L5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_store import Intent, intent_status_view

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


__all__ = ["DEADLINE_SEMANTICS", "project_restart_intent_consumer"]
