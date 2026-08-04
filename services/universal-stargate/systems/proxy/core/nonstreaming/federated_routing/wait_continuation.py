"""Continuation predicates and budget helpers for eviction wait loops.

Extracted from wait_logic when mode-aware still_transient checks would exceed
the SLOC gate; shared by finalize requeue and no-selection transient waits.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from ..constraint_retryable import constraint_failure_is_retryable

if TYPE_CHECKING:
    from ..context import RequestContext

ContinuationMode = Literal["busy_block", "execution_failure", "transient_capacity"]

_TRANSIENT_CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    }
)

_RESOURCE_CONSTRAINTS: frozenset[str] = frozenset({"has_enough_vram", "has_enough_ram"})


def clamp_eviction_wait_timeout(
    context: RequestContext,
    config_timeout: float,
) -> float:
    """Clamp eviction wait budget to the client capacity deadline when set."""
    deadline = getattr(context, "_capacity_deadline_mono", None)
    if deadline is not None:
        return min(config_timeout, max(0.0, deadline - time.monotonic()))
    return config_timeout


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


def continuation_still_transient(trace: Any, *, mode: ContinuationMode) -> bool:
    """Mode-aware predicate for whether the eviction wait loop should continue."""
    candidates = getattr(trace, "candidates", None) or ()
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
