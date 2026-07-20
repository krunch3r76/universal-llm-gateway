"""Signal string constants for Stargate routing waits: drain-initiated and eviction-wait started/cancelled/resolved/timeout signals, startup-queue queued/resolved/timeout signals, and model grace-period queued/resolved/timeout signals. Consumed by `routing_factories_eviction_waits_startup.py` and `routing_factories_model_grace.py`'s event factories."""

# ruff: noqa: N802

# ========================================
# Eviction wait queue (pre-selection queue)
# ========================================

ROUTING_EVICTION_WAIT_STARTED = "routing.eviction.wait.started"
"""
Request entered eviction wait queue (transient eviction_blocked_by_busy_models).

Payload: {
    "request_id": str,
    "model_id": str,
    "timeout_s": float,
    "queue_depth": int
}
"""

ROUTING_EVICTION_WAIT_RESOLVED = "routing.eviction.wait.resolved"
"""
Wait completed; selection succeeded after state change.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "waited_ms": int
}
"""

ROUTING_EVICTION_WAIT_TIMEOUT = "routing.eviction.wait.timeout"
"""
Eviction wait exited without a resolved placement.

`exit_reason` distinguishes two terminal conditions under one signal:
  - "budget_exhausted": waited the full eviction_wait_timeout_s budget
    without any candidate becoming feasible.
  - "non_transient": first-iteration exit because no candidate still carries
    eviction_blocked_by_busy_models — the state that made the wait-entry
    classifier call this transient no longer holds. Typically accompanies
    waited_ms ≈ 0.

`exit_constraint_summary` captures the trace candidates' constraint sets at
exit so post-hoc queries can identify which constraint replaced the transient
tag (usually can_fit_with_eviction on the non_transient path).

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int,
    "exit_reason": str,              # "budget_exhausted" | "non_transient"
    "exit_constraint_summary": list[dict],
        # [{gateway_id, constraints_failed: [str]}]
}
"""

ROUTING_EVICTION_WAIT_CANCELLED = "routing.eviction.wait.cancelled"
"""
Client disconnected or task cancelled during eviction wait.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int
}
"""

ROUTING_STARTUP_QUEUED = "routing.startup.queued"
"""
Request queued during startup window because no gateways have connected yet.

Emitted when Stargate is within its startup_queue_timeout_s window and the request
is held rather than immediately rejected with GATEWAY_DISCONNECTED.

Payload: {
    "request_id": str,
    "model_id": str,
    "uptime_s": float,
    "timeout_s": float
}
"""

ROUTING_STARTUP_RESOLVED = "routing.startup.resolved"
"""
Startup-queued request unblocked after a gateway connected.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "waited_ms": int,
    "uptime_s": float
}
"""

ROUTING_STARTUP_TIMEOUT = "routing.startup.timeout"
"""
Startup queue timed out with no gateway connecting before deadline.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int,
    "uptime_s": float
}
"""

ROUTING_MODEL_GRACE_QUEUED = "routing.model.grace.queued"
"""
Request held because the model exists only on unhealthy/circuit-broken gateways.

Model-scoped grace period: only requests for this model wait; other models
served by healthy gateways proceed immediately.

Payload: {
    "request_id": str,
    "model_id": str,
    "timeout_s": float,
    "unhealthy_gateway_ids": list[str]
}
"""

ROUTING_MODEL_GRACE_RESOLVED = "routing.model.grace.resolved"
"""
Model-scoped grace resolved after the model's gateway recovered.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "waited_ms": int
}
"""

ROUTING_MODEL_GRACE_TIMEOUT = "routing.model.grace.timeout"
"""
Model-scoped grace timed out: gateway did not recover within the window.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int
}
"""

ROUTING_DRAIN_INITIATED = "routing.drain.initiated"
"""
Starvation-triggered admission drain initiated for one or more blocking models.

Emitted by _wait_and_retry_selection when a waiter blocked on
eviction_blocked_by_busy_models has been starving for longer than
starvation_drain_threshold_s. The wait loop calls capacity_pool.pause_admission
for each in-flight routing key on every busy-blocked candidate gateway
(excluding the target model). In-flight drains naturally; the eviction
planner then sees no in-flight keys and succeeds on its next retry.

Payload: {
    "request_id": str,           # starved request triggering the drain
    "target_model_id": str,      # model the starved request is trying to load
    "gateway_ids": list[str],    # gateways whose in-flight traffic is being paused
    "drained_model_ids": list[str], # routing keys whose admission was paused
    "duration_s": float,         # pause duration per model
    "starved_for_ms": int,       # how long the waiter had been blocked
}
"""
