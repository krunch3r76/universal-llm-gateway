"""Machine-readable restart-intent reason codes and terminal projection.

Codes may carry a human detail after ``:`` (``drain_probe_exception:ReadTimeout``).
``TERMINAL_STATUS_PROJECTION`` is what ``busy_status.restart_intent_last`` emits.
``force_requested`` maps to ``fired``: the ceiling already committed the restart.
A cancelled row whose status_reason code is ``ttl_expired`` projects as
``expired`` (the store status stays ``cancelled``; expiry is not a timeout row).
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
ARMED = "armed"
KILL_COMMIT = "kill_commit"
SUPERVISOR_EXCEPTION = "supervisor_exception"
KILL_FAILED = "kill_failed"
LIFECYCLE_UNCONFIRMED = "lifecycle_unconfirmed"
BEGIN_DRAIN_UNREACHABLE = "begin_drain_unreachable"
GENERATION_GONE = "generation_gone"
EPOCH_CHECK_OK = "epoch_check_ok"
EPOCH_CHECK_MISMATCH = "epoch_check_mismatch"
TARGET_GONE = "target_gone"
FORCE_START_PENDING = "force_start_pending"
FORCE_START_COMPLETED = "force_start_completed"
ACTIVATION_NOT_APPLICABLE = "activation_not_applicable"
ACTIVATION_VERIFY_ARMED = "activation_verify_armed"
ACTIVATION_VALIDATED = "activation_validated"
RECONCILE_RESUME_FAILED = "reconcile_resume_failed"
UNSPECIFIED_TRANSITION = "unspecified_transition"

ALL_REASON_CODES = frozenset(
    {
        DRAIN_PROBE_EXCEPTION,
        BLOCKED_BY_JOB,
        IDLE_CEILING_REACHED,
        IDLE_OBSERVED,
        LIFECYCLE_EXCEPTION,
        LIFECYCLE_COMPLETED,
        CANCELLED_BY_OPERATOR,
        TTL_EXPIRED,
        ARMED,
        KILL_COMMIT,
        SUPERVISOR_EXCEPTION,
        KILL_FAILED,
        LIFECYCLE_UNCONFIRMED,
        BEGIN_DRAIN_UNREACHABLE,
        GENERATION_GONE,
        EPOCH_CHECK_OK,
        EPOCH_CHECK_MISMATCH,
        TARGET_GONE,
        FORCE_START_PENDING,
        FORCE_START_COMPLETED,
        ACTIVATION_NOT_APPLICABLE,
        ACTIVATION_VERIFY_ARMED,
        ACTIVATION_VALIDATED,
        RECONCILE_RESUME_FAILED,
        UNSPECIFIED_TRANSITION,
    }
)

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
