"""Stargate scheduling routing events — split module (routing_signal_constants_decisions.py)."""

# ruff: noqa: N802

# ========================================
# Routing Decision Event Signals
# ========================================

ROUTING_DECISION = "scheduler.routing.decided"
"""
Routing decision trace emitted by the DecisionEngine.
Emitted for every selection attempt, including no-selection outcomes that may
still recover via pre-route wait or admission wait.

Payload: {
    "model_id": str,                    # Model being routed
    "original_model_id": str | None,    # Original request model ID
    "selected_gateway": str | None,     # Selected gateway name
    "selection_reason": str,            # Why this gateway was selected
    "selection_tier": str | None,       # T0/T1/T2 tier name
    "candidate_count": int,             # Total candidates evaluated
    "feasible_count": int,              # Number of feasible candidates
    "evaluation_time_ms": float,        # Time spent in decision engine
    "request_id": str | None,           # Proxy request ID for tracing
    "timestamp": float,                 # Unix timestamp

    # Optional detailed trace (expensive, enable for debugging)
    "candidates": list[dict] | None,    # Full candidate details
}
"""

ROUTING_DECISION_FAILED = "scheduler.routing.failed"
"""
Routing failed terminally for the request.
Emitted only after all retryable routing/admission wait paths are exhausted and
the request will raise a no-capacity / no-feasible-gateway error.

Payload: {
    "model_id": str,
    "original_model_id": str | None,
    "candidate_count": int,
    "evaluation_time_ms": float,
    "request_id": str | None,
    "timestamp": float,
    "reason": str
}
"""

ROUTING_QUEUED = "scheduler.routing.queued"
"""
Request entered pre-routing wait queue (retryable constraint).
Emitted once when a request begins waiting for capacity signal.

Payload: {
    "request_id": str,
    "model_id": str,
    "constraint": str,
    "gateway_id": str | None,
    "timestamp": float
}
"""

ROUTING_DEQUEUED = "scheduler.routing.dequeued"
"""
Request admitted after waiting for capacity signal.
Emitted when gateway.resource.updated unblocks a queued request.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "wait_ms": float,
    "timestamp": float
}
"""

ROUTING_TIMEOUT = "scheduler.routing.timeout"
"""
Pre-routing queue timeout expired before capacity became available.

Payload: {
    "request_id": str,
    "model_id": str,
    "constraint": str,
    "wait_ms": float,
    "timestamp": float
}
"""

ROUTING_OVERFLOW_TRIGGERED = "routing.overflow.triggered"
"""
Non-sticky overflow branch selected an alternate gateway.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "to_gateway": str,
    "reason": str
}
"""

ROUTING_OVERFLOW_FAILED = "routing.overflow.failed"
"""
Non-sticky overflow attempt contributed to a terminal routing failure.

Payload: {
    "request_id": str,
    "model_id": str,
    "tried_gateways": list[str],
    "reason": str
}
"""

MODEL_CAPACITY_OVERFLOW_ASSIGNED = "model.capacity.overflow.assigned"
"""
Admission assigned request to overflow gateway after spillover decision.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "to_gateway": str,
    "depth_before": int
}
"""

MODEL_LOAD_OVERFLOW_STARTED = "model.load.overflow.started"
"""
Cold-load started on overflow gateway chosen by spillover.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "reason": str
}
"""
