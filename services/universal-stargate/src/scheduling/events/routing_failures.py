"""Routing failure diagnostic event signals.

Covers specific failure modes that cause routing to return 503 or non-retryable
errors: resource data gaps, infeasibility, eviction blocks, upstream exclusion,
capacity divergence, and cold-load pre-seeding.

Signals:
    routing.resource.data.missing — model in catalog but missing resource data
    routing.model.infeasible — every candidate gateway is infeasible
    routing.eviction.blocked.busy — eviction blocked by busy loaded models
    routing.eviction.insufficient.permanent — VRAM permanently insufficient
    routing.upstream.all.excluded — all gateways excluded by upstream failures
    routing.capacity.divergence — telemetry/CapacityPool state mismatch
    routing.capacity.preseeded — CapacityPool pre-seeded for cold load
"""

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
    "gateway_id": str,
    "loaded_count": int,
    "busy_count": int,
    "vram_free": int
}
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
    "failed_constraints": list[str]
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
CapacityPool pre-seeded for a cold-load model from catalog model_details.

Emitted when a request triggers a cold load and CapacityPool is seeded with
expected capacity BEFORE the model finishes loading.  This closes the
cold-load bypass that previously let unlimited requests flood the gateway.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "expected_capacity": int,
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
    gateway_constraints: list[dict[str, object]],
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
) -> Event:
    """
    Create ROUTING_EVICTION_BLOCKED_BUSY event.

    Args:
        request_id: Request that failed routing
        model_id: Target model identifier
        gateway_id: Gateway where eviction was evaluated
        loaded_count: Number of loaded models on gateway
        busy_count: Number of loaded models currently busy
        vram_free: Free VRAM in MB

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
        },
    )


@event_factory
def RoutingEvictionInsufficientPermanent(
    request_id: str,
    model_id: str,
    gateway_id: str,
    reason: str,
    failed_constraints: list[str],
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
    return Event(
        signal=ROUTING_EVICTION_INSUFFICIENT_PERMANENT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "reason": reason,
            "failed_constraints": failed_constraints,
        },
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
    busy_models_state: str,
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
    expected_capacity: int,
) -> Event:
    """
    Create ROUTING_CAPACITY_PRESEEDED event.

    Emitted when a cold-load request pre-seeds CapacityPool with expected
    capacity from catalog model_details, closing the cold-load bypass.

    Args:
        request_id: Request that triggered the pre-seed
        model_id: Model being cold-loaded
        gateway_id: Target gateway
        expected_capacity: Slots pre-seeded from max_concurrent_requests

    Returns:
        Event with RoutingCapacityPreseeded signal
    """
    return Event(
        signal=ROUTING_CAPACITY_PRESEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "expected_capacity": expected_capacity,
        },
    )
