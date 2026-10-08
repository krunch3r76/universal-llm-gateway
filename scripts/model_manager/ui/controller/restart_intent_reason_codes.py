"""Machine-readable restart-intent reason codes and terminal projection.

Codes may carry a human detail after ``:`` (``drain_probe_exception:ReadTimeout``).
``TERMINAL_STATUS_PROJECTION`` is what ``busy_status.restart_intent_last`` emits.
``force_requested`` maps to ``fired``: the ceiling already committed the restart.
"""

from __future__ import annotations

from .restart_intent_states import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_TIMEOUT,
)

DRAIN_PROBE_EXCEPTION = "drain_probe_exception"
BLOCKED_BY_JOB = "blocked_by_job"
IDLE_CEILING_REACHED = "idle_ceiling_reached"
IDLE_OBSERVED = "idle_observed"
LIFECYCLE_EXCEPTION = "lifecycle_exception"
LIFECYCLE_COMPLETED = "lifecycle_completed"
CANCELLED_BY_OPERATOR = "cancelled_by_operator"
TTL_EXPIRED = "ttl_expired"

# Store status → restart_intent_last.status. Readers never see raw store names.
TERMINAL_STATUS_PROJECTION = {
    STATUS_COMPLETED: "fired",
    STATUS_FORCE_REQUESTED: "fired",
    STATUS_FAILED: "failed",
    STATUS_ACTIVATION_UNVERIFIED: "failed",
    STATUS_TIMEOUT: "expired",
    STATUS_CANCELLED: "cancelled",
}


def reason_code(code: str, detail: str = "") -> str:
    text = detail.strip()
    if not text:
        return code
    return f"{code}:{text}"
