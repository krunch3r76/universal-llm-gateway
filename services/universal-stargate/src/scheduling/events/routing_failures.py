# ruff: noqa: N802
"""Routing failure diagnostic event signals.

Covers specific failure modes that cause routing to return 503 or non-retryable
errors: resource data gaps, infeasibility, eviction blocks, upstream exclusion,
capacity divergence, cold-load loading placeholder seeding, and capacity slot
leak recovery.

Signals:
    routing.resource.data.missing — model in catalog but missing resource data
    routing.model.infeasible — every candidate gateway is infeasible
    routing.eviction.blocked.busy — eviction blocked by busy loaded models
    routing.eviction.insufficient.permanent — VRAM permanently insufficient
    routing.upstream.all.excluded — all gateways excluded by upstream failures
    routing.capacity.divergence — telemetry/CapacityPool state mismatch
    routing.capacity.preseeded — CapacityPool loading placeholder for cold load
    routing.overflow.triggered — non-sticky spillover path selected
    routing.overflow.failed — overflow attempt ended in terminal routing failure
    capacity.slot.leak.recovered — cancellation race slot recovery in CapacityPool
"""

from typing import Any, Literal

from universal_event_bus import Event, event_factory

# ========================================
# Routing Failure Event Signals
# ========================================

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


# ========================================
# Factory Functions
# ========================================


@event_factory
def RoutingResourceDataMissing(
    request_id: str,
    model_id: str,
    gateway_ids: list[str],
) -> Event:
    """
    Create ROUTING_RESOURCE_DATA_MISSING event.

    Emitted when model is in gateway available_models (catalog) but
    absent from model_details (no resource data). This causes T0_INFEASIBLE
    via missing_gateway_resource_data constraint — routing fails despite
    model appearing in /v1/models.

    Distinguishes startup resource gap from genuine MODEL_NOT_FOUND.

    Args:
        request_id: Request that failed routing
        model_id: Model that has catalog entry but no resource data
        gateway_ids: Gateways that have model in catalog but no resource data

    Returns:
        Event with RoutingResourceDataMissing signal
    """
    return Event(
        signal=ROUTING_RESOURCE_DATA_MISSING,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_ids": gateway_ids,
        },
    )


@event_factory
def RoutingModelInfeasible(
    request_id: str,
    model_id: str,
    gateway_constraints: list[dict[str, Any]],
    excluded_gateway_ids: list[str],
) -> Event:
    """
    Create ROUTING_MODEL_INFEASIBLE event.

    Model exists in at least one gateway catalog but every candidate is
    infeasible (capacity, circuit breaker, resource constraints, etc.).
    Accompanies NO_FEASIBLE_GATEWAY (503) error response.

    Args:
        request_id: Request that failed routing
        model_id: Model that exists but cannot be served
        gateway_constraints: Per-gateway constraint failures
        excluded_gateway_ids: Gateways excluded by retry logic

    Returns:
        Event with RoutingModelInfeasible signal
    """
    return Event(
        signal=ROUTING_MODEL_INFEASIBLE,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_constraints": gateway_constraints,
            "excluded_gateway_ids": excluded_gateway_ids,
        },
    )


@event_factory
def RoutingEvictionBlockedBusy(
    request_id: str,
    model_id: str,
    gateway_id: str,
    loaded_count: int,
    busy_count: int,
    vram_free: int,
    candidate_breakdown: list[dict],
) -> Event:
    """
    Create ROUTING_EVICTION_BLOCKED_BUSY event.

    Args:
        request_id: Request that failed routing
        model_id: Target model identifier
        gateway_id: Primary candidate gateway (the one picked for the summary)
        loaded_count: Number of loaded models on primary candidate
        busy_count: Number of loaded models currently busy on primary candidate
        vram_free: Free VRAM on primary candidate (MB)
        candidate_breakdown: Per-candidate snapshot of loaded_count, busy_count,
            loading_count, vram_free, constraints_failed. Additive — enables
            post-hoc correlation between wait-entry state and wait-exit flips.

    Returns:
        Event with RoutingEvictionBlockedBusy signal
    """
    return Event(
        signal=ROUTING_EVICTION_BLOCKED_BUSY,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "loaded_count": loaded_count,
            "busy_count": busy_count,
            "vram_free": vram_free,
            "candidate_breakdown": candidate_breakdown,
        },
    )


@event_factory
def RoutingEvictionInsufficientPermanent(
    request_id: str,
    model_id: str,
    gateway_id: str,
    reason: str,
    failed_constraints: list[str],
    verdict_class: str | None = None,
    needed_mb: int | None = None,
    footprint_est_mb: int | None = None,
    margin_mb: int | None = None,
    attainable_mb: int | None = None,
    reserved_mb: int | None = None,
) -> Event:
    """
    Create ROUTING_EVICTION_INSUFFICIENT_PERMANENT event.

    Args:
        request_id: Request that failed routing
        model_id: Target model identifier
        gateway_id: Gateway where eviction was evaluated
        reason: Human-readable permanent insufficiency reason
        failed_constraints: Constraint names that could not be satisfied

    Returns:
        Event with RoutingEvictionInsufficientPermanent signal
    """
    payload = {
        "request_id": request_id,
        "model_id": model_id,
        "gateway_id": gateway_id,
        "reason": reason,
        "failed_constraints": failed_constraints,
    }
    admission_fields = {
        "verdict_class": verdict_class,
        "needed_mb": needed_mb,
        "footprint_est_mb": footprint_est_mb,
        "margin_mb": margin_mb,
        "attainable_mb": attainable_mb,
        "reserved_mb": reserved_mb,
    }
    payload.update({k: v for k, v in admission_fields.items() if v is not None})
    return Event(
        signal=ROUTING_EVICTION_INSUFFICIENT_PERMANENT,
        payload=payload,
    )


@event_factory
def RoutingUpstreamAllExcluded(
    request_id: str,
    model_id: str,
    excluded_gateway_ids: list[str],
) -> Event:
    """
    Create ROUTING_UPSTREAM_ALL_EXCLUDED event.

    All gateways for the model have been excluded due to upstream (5xx) failures.
    Emitted immediately before a non-retryable fail-fast response.

    Args:
        request_id: Request that exhausted all gateways
        model_id: Model whose gateways all failed upstream
        excluded_gateway_ids: Gateways that returned upstream errors

    Returns:
        Event with RoutingUpstreamAllExcluded signal
    """
    return Event(
        signal=ROUTING_UPSTREAM_ALL_EXCLUDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "excluded_gateway_ids": excluded_gateway_ids,
        },
    )


@event_factory
def RoutingCapacityDivergence(
    request_id: str,
    model_id: str,
    gateway_id: str,
    busy_models_state: Literal["busy", "idle"],
    capacity_pool_available: int,
    capacity_pool_in_flight: int,
    capacity_pool_max: int,
) -> Event:
    """
    Create ROUTING_CAPACITY_DIVERGENCE event.

    Emitted when telemetry-derived busy_models and CapacityPool slot state disagree.
    Primary purpose is stale telemetry observability; routing correctness still
    relies on CapacityPool admission.

    Args:
        request_id: Request that triggered divergence detection
        model_id: Divergent model
        gateway_id: Gateway with divergent state
        busy_models_state: Telemetry busy/idle claim
        capacity_pool_available: Available slots from CapacityPool
        capacity_pool_in_flight: Current in-flight requests in CapacityPool
        capacity_pool_max: Max concurrent slots in CapacityPool

    Returns:
        Event with RoutingCapacityDivergence signal
    """
    return Event(
        signal=ROUTING_CAPACITY_DIVERGENCE,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "busy_models_state": busy_models_state,
            "capacity_pool_available": capacity_pool_available,
            "capacity_pool_in_flight": capacity_pool_in_flight,
            "capacity_pool_max": capacity_pool_max,
        },
    )


@event_factory
def RoutingCapacityPreseeded(
    request_id: str,
    model_id: str,
    gateway_id: str,
    placeholder_capacity: int,
    catalog_capacity: int,
) -> Event:
    """
    Create ROUTING_CAPACITY_PRESEEDED event.

    Emitted when a cold-load request seeds CapacityPool with a bounded
    loading-phase placeholder instead of the model's full post-load capacity.

    Args:
        request_id: Request that triggered the pre-seed
        model_id: Model being cold-loaded
        gateway_id: Target gateway
        placeholder_capacity: Slots exposed while the model is still loading
        catalog_capacity: Full max_concurrent_requests from model_details

    Returns:
        Event with RoutingCapacityPreseeded signal
    """
    return Event(
        signal=ROUTING_CAPACITY_PRESEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "placeholder_capacity": placeholder_capacity,
            "catalog_capacity": catalog_capacity,
        },
    )


@event_factory
def RoutingOverflowTriggered(
    request_id: str,
    model_id: str,
    from_gateway: str,
    to_gateway: str,
    reason: str,
) -> Event:
    """Create ROUTING_OVERFLOW_TRIGGERED event.

    Emitted when the non-sticky overflow path excludes the primary gateway and
    finds a feasible alternate gateway for the same request.

    Args:
        request_id: Request that triggered overflow routing
        model_id: Model being routed
        from_gateway: Saturated primary gateway selected first
        to_gateway: Alternate gateway selected by the overflow pass
        reason: Why the overflow path was taken

    Returns:
        Event with RoutingOverflowTriggered signal
    """
    return Event(
        signal=ROUTING_OVERFLOW_TRIGGERED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "from_gateway": from_gateway,
            "to_gateway": to_gateway,
            "reason": reason,
        },
    )


@event_factory
def RoutingOverflowFailed(
    request_id: str,
    model_id: str,
    from_gateway: str,
    reason: str,
) -> Event:
    """Create ROUTING_OVERFLOW_FAILED event.

    Emitted only when an earlier non-sticky overflow attempt is part of the
    final terminal routing failure.

    Args:
        request_id: Request that attempted overflow routing
        model_id: Model being routed
        from_gateway: Primary gateway that was excluded from the overflow pass
        reason: Why overflow routing could not complete

    Returns:
        Event with RoutingOverflowFailed signal
    """
    return Event(
        signal=ROUTING_OVERFLOW_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "from_gateway": from_gateway,
            "reason": reason,
        },
    )


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


@event_factory
def CapacitySlotLeakRecovered(
    request_id: str,
    gateway_id: str,
    model_id: str,
    snapshot: dict[str, Any],
) -> Event:
    """Create CAPACITY_SLOT_LEAK_RECOVERED event.

    Emitted by CapacityPool._recover_leaked_slot when the cancellation race
    in _wait_for_slot is detected: _dispatch resolved the future (incrementing
    in_flight) but the waiter's task was cancelled before the CapacityToken
    was created.  Without recovery, the slot leaks permanently.

    Args:
        request_id: Request whose slot was leaked and recovered
        gateway_id: Gateway where the slot was allocated
        model_id: Model the slot was reserved for
        snapshot: CapacityPool diagnostic snapshot at recovery time

    Returns:
        Event with CapacitySlotLeakRecovered signal
    """
    return Event(
        signal=CAPACITY_SLOT_LEAK_RECOVERED,
        payload={
            "request_id": request_id,
            "gateway_id": gateway_id,
            "model_id": model_id,
            "snapshot": snapshot,
        },
    )
