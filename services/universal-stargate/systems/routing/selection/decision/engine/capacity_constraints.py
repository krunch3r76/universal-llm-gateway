"""Helpers that classify temporary capacity constraints for sticky routing decisions.

The decision engine uses these checks to distinguish retryable saturation from
permanent infeasibility. Sticky guard logic depends on this classification to
enforce single-gateway placement invariants without dropping availability signals.
"""

from __future__ import annotations

from ..types import GatewayCandidate

# Capacity constraints that indicate temporary unavailability (retryable).
# Distinguished from permanent failures (is_healthy, has_model_available)
# to allow sticky guard to only block on transient conditions.
# circuit_breaker: temporary (OPEN→HALF_OPEN after recovery_timeout); model IS
# available but gateway is isolated. Treat as transient, not permanent failure.
CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "has_enough_vram",
        "has_enough_ram",
        "compute_type_capacity",
        "has_gateway_capacity",
        "circuit_breaker",
    }
)


def is_capacity_constrained(candidate: GatewayCandidate) -> bool:
    """Return whether infeasibility is caused by temporary capacity pressure.

    This predicate only inspects constraint names from feasibility evaluation.
    Callers use it to gate retries for sticky models when the bound gateway is
    saturated but still otherwise healthy and model-capable.
    """
    return any(
        f.constraint in CAPACITY_CONSTRAINTS for f in candidate.constraints_failed
    )
