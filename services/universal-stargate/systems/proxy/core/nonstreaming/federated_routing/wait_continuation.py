"""Continuation predicates and budget helpers for eviction wait loops.

Extracted from wait_logic when mode-aware still_transient checks would exceed
the SLOC gate; shared by finalize requeue and no-selection transient waits.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from universal_logging import get_logger

from ..capacity_deadline import embedding_capacity_acquire_deadline_mono
from ..constraint_retryable import constraint_failure_is_retryable

if TYPE_CHECKING:
    from systems.routing.selection.decision.eviction_cooldown_policy import (
        CooldownOverrideKey,
    )

    from ..context import RequestContext

logger = get_logger(__name__)

ContinuationMode = Literal[
    "busy_block",
    "execution_failure",
    "transient_capacity",
    "cooldown_blocked",
]

_TRANSIENT_CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    }
)

_RESOURCE_CONSTRAINTS: frozenset[str] = frozenset({"has_enough_vram", "has_enough_ram"})


def _read_positive_float(
    config: dict[str, object], key: str, default: float, label: str
) -> float:
    """Read a positive float from *config*; log ERROR on missing or invalid values."""
    raw = config.get(key)
    if raw is None:
        logger.error("%s missing in config; using %.0fs default", label, default)
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.error("Invalid %s=%r; using %.0fs default", label, raw, default)
        return default
    if value <= 0:
        logger.error(
            "Invalid %s=%r (must be > 0); using %.0fs default", label, raw, default
        )
        return default
    return value


def get_eviction_wait_budget_s(routing_config: dict[str, Any] | None) -> float:
    """Return per-request eviction wait budget (seconds).

    Source of truth: routing policy ``eviction_wait_timeout_s`` (default 300s).
    Nested ``routing.eviction_wait_timeout_s`` is honored when the top-level key
    is absent. Invalid/missing values log ERROR once per read (defaults policy).
    """
    rc = routing_config or {}
    cfg_for_read: dict[str, object] = {}
    if "eviction_wait_timeout_s" in rc:
        cfg_for_read["eviction_wait_timeout_s"] = rc["eviction_wait_timeout_s"]
    else:
        nested = rc.get("routing")
        if isinstance(nested, dict) and "eviction_wait_timeout_s" in nested:
            cfg_for_read["eviction_wait_timeout_s"] = nested["eviction_wait_timeout_s"]
    return _read_positive_float(
        cfg_for_read,
        "eviction_wait_timeout_s",
        300.0,
        "eviction_wait_timeout_s",
    )


def arm_or_remaining_eviction_budget(
    context: RequestContext,
    config_timeout: float,
) -> float:
    """Return remaining seconds on this request's shared eviction+load budget.

    Invariant: exactly one ``eviction_wait_timeout_s`` budget per request,
    clock starting on the first eviction or transient-capacity wait. The budget
    is shared across finalize requeues, no-selection waits, and upstream failover
    attempts (the retry loop does not reset ``_eviction_wait_deadline_mono``).
    Pre-route queue time does not consume this budget. Embeddings (no disconnect
    backstop) additionally cap the armed deadline by ``_capacity_deadline_mono``.
    """
    now = time.monotonic()
    deadline = getattr(context, "_eviction_wait_deadline_mono", None)
    if deadline is None:
        deadline = now + config_timeout
        cap = embedding_capacity_acquire_deadline_mono(context)
        if cap is not None:
            deadline = min(deadline, cap)
        context._eviction_wait_deadline_mono = deadline
    return max(0.0, deadline - now)


def _is_permanent_resource_or_structural(candidate: Any) -> bool:
    """Return True for non-retryable resource or structural constraint verdicts."""
    failures = getattr(candidate, "constraints_failed", ()) or ()
    for failure in failures:
        details = getattr(failure, "details", None) or {}
        if details.get("verdict_class") == "insufficient_structural":
            return True
        if (
            failure.constraint == "can_fit_with_eviction"
            and not constraint_failure_is_retryable(failure)
        ):
            return True
    failed = {failure.constraint for failure in failures}
    return bool(failed & _RESOURCE_CONSTRAINTS) and "can_fit_with_eviction" in failed


def _candidate_has_transient_continuation_signal(candidate: Any) -> bool:
    """True when any failure is transient-retryable or transient-capacity class."""
    for failure in getattr(candidate, "constraints_failed", ()) or ():
        if failure.constraint in _TRANSIENT_CAPACITY_CONSTRAINTS:
            return True
        if constraint_failure_is_retryable(failure):
            return True
    return False


def continuation_still_transient(
    trace: Any,
    *,
    mode: ContinuationMode,
    cooldown_hold_key: CooldownOverrideKey | None = None,
) -> bool:
    """Mode-aware predicate for whether the eviction wait loop should continue."""
    if mode == "cooldown_blocked":
        if cooldown_hold_key is not None:
            from systems.routing.selection.decision.eviction_cooldown_policy import (
                oscillation_hold_remaining_s,
            )

            if oscillation_hold_remaining_s(cooldown_hold_key) > 0.0:
                return True
        mode = "execution_failure"

    candidates = getattr(trace, "candidates", ()) or ()
    if mode == "busy_block":
        return any(
            any(
                failure.constraint == "eviction_blocked_by_busy_models"
                for failure in getattr(candidate, "constraints_failed", ()) or ()
            )
            for candidate in candidates
        )

    return any(
        _candidate_has_transient_continuation_signal(candidate)
        and not _is_permanent_resource_or_structural(candidate)
        for candidate in candidates
    )
