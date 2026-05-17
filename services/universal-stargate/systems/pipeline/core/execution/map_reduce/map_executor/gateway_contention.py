"""Gateway contention detection for map partial-failure diagnostics.

Detects repeated gateway_id appearances across IterationResult records.
A gateway that appears more than once in the result set is treated as a
contention / serialization signal and is reported in MapPartialFailureError
so operators can correlate partial failures with overloaded gateways.
"""

from __future__ import annotations

from collections import Counter

from ..iteration_state import IterationResult


def serialized_gateways(
    iteration_results: list[IterationResult],
) -> tuple[str, ...] | None:
    """Return gateways that appear more than once (contention signal).

    Scans the provided results for non-None gateway_id values, counts
    occurrences per gateway, and returns a tuple of those with count > 1.
    The tuple is sorted by first appearance order from the Counter (stable
    since Python 3.7 dict order).

    Returns None when no gateway repeated (the common success path and the
    single-failure case). Callers use the None vs. tuple distinction to
    populate the gateway_serialization field of the error.

    This is a pure function; it does not mutate its input.
    """
    gateway_counts = Counter(r.gateway_id for r in iteration_results if r.gateway_id)
    serialized = tuple(gw for gw, count in gateway_counts.items() if count > 1)
    return serialized if serialized else None
