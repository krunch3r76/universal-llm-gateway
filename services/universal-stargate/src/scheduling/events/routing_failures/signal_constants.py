"""Signal string constants for `routing_failures` scheduling events (capacity divergence, preseeding, and slot-leak recovery; eviction-blocked and insufficient-permanent-capacity failures; infeasible-model and upstream-all-excluded routing failures; overflow trigger/failure). Re-exported via the `routing_failures` package facade for `factories.py`."""

# ruff: noqa: N802

ROUTING_RESOURCE_DATA_MISSING = "routing.resource.data.missing"
"""
Model is in gateway catalog (available_models) but missing from model_details.

Emitted when routing fails with missing_gateway_resource_data constraint.
Distinguishes startup resource-gap from genuine MODEL_NOT_FOUND.

Diagnostic query:
    jq 'select(.signal == "routing.resource.data.missing")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_ids": list[str]  # gateways that have model in catalog but no resource data
}
"""

ROUTING_MODEL_INFEASIBLE = "routing.model.infeasible"
"""
Model exists in gateway catalogs but every candidate gateway is infeasible.

Emitted when routing returns NO_FEASIBLE_GATEWAY (503, retryable).
Carries per-gateway constraint details for diagnosis.

Diagnostic query:
    jq 'select(.signal == "routing.model.infeasible")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_constraints": list[dict]  # per-gateway constraint failures
    "excluded_gateway_ids": list[str]  # gateways excluded by retry logic
}
"""

ROUTING_EVICTION_BLOCKED_BUSY = "routing.eviction.blocked.busy"
"""
Eviction is temporarily blocked because all loaded models on a gateway are busy.

Emitted when routing cannot create an eviction plan now, but the model can fit
once currently busy loaded models become idle and evictable.

Diagnostic query:
    jq 'select(.signal == "routing.eviction.blocked.busy")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,                      # primary candidate (back-compat)
    "loaded_count": int,                    # primary candidate (back-compat)
    "busy_count": int,                      # primary candidate (back-compat)
    "vram_free": int,                       # primary candidate (back-compat)
    "candidate_breakdown": list[dict],      # per-candidate snapshot:
        # {gateway_id, loaded_count, busy_count, loading_count, vram_free,
        #  constraints_failed: [str]}
}

The `candidate_breakdown` field is additive: consumers that read only the
primary fields continue to work. `loading_count` is included so entry-time
loading state can be correlated with wait-exit constraint flips.
"""

ROUTING_EVICTION_INSUFFICIENT_PERMANENT = "routing.eviction.insufficient.permanent"
"""
Eviction cannot make enough room — permanent hardware constraint.

Emitted immediately before RESOURCE_UNAVAILABLE when routing determines that
VRAM/RAM are insufficient even after considering eviction.

Diagnostic query:
    jq 'select(.signal == "routing.eviction.insufficient.permanent")'

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "reason": str,
    "failed_constraints": list[str],
    "verdict_class": str | None,
    "needed_mb": int | None,
    "footprint_est_mb": int | None,
    "margin_mb": int | None,
    "attainable_mb": int | None,
    "reserved_mb": int | None,
}
"""

ROUTING_UPSTREAM_ALL_EXCLUDED = "routing.upstream.all.excluded"
"""
All gateways for a model have been excluded due to upstream (5xx) failures.

Emitted immediately before failing non-retryably. Distinguishes "no alternative
gateway" from retryable infeasibility — these requests should not be retried on
the same gateway.

Diagnostic query:
    jq 'select(.signal == "routing.upstream.all.excluded")'

Payload: {
    "request_id": str,
    "model_id": str,
    "excluded_gateway_ids": list[str]  # gateways that returned upstream errors
}
"""

ROUTING_CAPACITY_DIVERGENCE = "routing.capacity.divergence"
"""
Telemetry busy_models disagrees with master-local CapacityPool.

Emitted when telemetry marks a model as busy while CapacityPool reports
available slots on the selected gateway/model.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "busy_models_state": str,         # "busy" | "idle"
    "capacity_pool_available": int,
    "capacity_pool_in_flight": int,
    "capacity_pool_max": int,
}
"""

ROUTING_CAPACITY_PRESEEDED = "routing.capacity.preseeded"
"""
CapacityPool receives a bounded loading-phase placeholder for a cold-load model.

Emitted when a request triggers a cold load and CapacityPool is seeded with
placeholder capacity (not full post-load concurrency) BEFORE the model finishes
loading. This closes the cold-load bypass while avoiding herd admission.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "placeholder_capacity": int,
    "catalog_capacity": int,
}
"""

ROUTING_OVERFLOW_TRIGGERED = "routing.overflow.triggered"
"""
Non-sticky request selected an alternate gateway due to primary saturation.

Emitted when a second decision pass (excluding the original selected gateway)
finds a feasible alternate and spillover is triggered.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "to_gateway": str,
    "reason": str,
}
"""

ROUTING_OVERFLOW_FAILED = "routing.overflow.failed"
"""
Non-sticky overflow attempt contributed to a terminal routing failure.

Emitted only when spillover was attempted earlier and the request later still
fails terminally during routing rejection.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "reason": str,
}
"""


CAPACITY_SLOT_LEAK_RECOVERED = "capacity.slot.leak.recovered"
"""
Cancellation race in CapacityPool._wait_for_slot recovered a leaked slot.

Canary signal: any occurrence means a waiter was cancelled/timed out AFTER
_dispatch had already admitted it (incremented in_flight, resolved the future).
The slot was recovered by _recover_leaked_slot to prevent permanent capacity loss.

Monitoring: non-zero rate under load is expected (asyncio scheduling race);
sustained high rate may indicate excessive cancellation or timeout tuning issues.

Diagnostic query:
    jq 'select(.signal == "capacity.slot.leak.recovered")'

Payload: {
    "request_id": str,
    "gateway_id": str,
    "model_id": str,
    "snapshot": dict       # CapacityPool.get_snapshot() at recovery time
}
"""
