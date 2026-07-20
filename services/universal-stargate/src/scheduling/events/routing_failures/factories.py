"""Factory functions for `routing_failures` scheduling events. Builds `Event` objects for capacity divergence/preseeding/leak-recovery, eviction-blocked and insufficient-permanent-capacity failures, infeasible-model and upstream-all-excluded routing failures, and overflow trigger/failure signals, for callers importing from `src.scheduling.events.routing_failures`."""

# ruff: noqa: N802

from typing import Any, Literal

from universal_event_bus import Event, event_factory

from .signal_constants import (
    CAPACITY_SLOT_LEAK_RECOVERED,
    ROUTING_CAPACITY_DIVERGENCE,
    ROUTING_CAPACITY_PRESEEDED,
    ROUTING_EVICTION_BLOCKED_BUSY,
    ROUTING_EVICTION_INSUFFICIENT_PERMANENT,
    ROUTING_MODEL_INFEASIBLE,
    ROUTING_OVERFLOW_FAILED,
    ROUTING_OVERFLOW_TRIGGERED,
    ROUTING_RESOURCE_DATA_MISSING,
    ROUTING_UPSTREAM_ALL_EXCLUDED,
)


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
