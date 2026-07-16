"""Shared transient/permanent classification from feasibility constraint failures."""

from __future__ import annotations

from typing import Any


def constraint_failure_is_retryable(failure: Any) -> bool:
    """Return whether a constraint failure should be queued or retried.

    Feasibility stamps ``details.retryable`` on eviction-class constraints;
    that flag is authoritative when present. Constraint names are a fallback
    only for older traces missing the stamp.
    """
    details = getattr(failure, "details", None) or {}
    if "retryable" in details:
        return bool(details["retryable"])

    verdict_class = details.get("verdict_class")
    if verdict_class == "insufficient_structural":
        return False
    if verdict_class in ("insufficient_transient", "insufficient_margin"):
        return True

    constraint = getattr(failure, "constraint", None)
    if constraint == "eviction_blocked_by_busy_models":
        return True
    if constraint == "can_fit_with_eviction":
        return False
    return False


def extract_retryable_constraint(trace: Any) -> str | None:
    """Return the first queueable capacity constraint from a decision trace, if any."""
    for candidate in getattr(trace, "candidates", ()):
        for failure in getattr(candidate, "constraints_failed", ()):
            if constraint_failure_is_retryable(failure):
                return getattr(failure, "constraint", None)
    return None
