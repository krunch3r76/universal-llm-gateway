"""Stargate scheduling routing events — split module"
"(routing_factories_eviction_hysteresis.py)."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_eviction_hysteresis import (
    EVICTION_COOLDOWN_APPLIED,
    EVICTION_COOLDOWN_BLOCKED,
    EVICTION_COOLDOWN_OVERRIDDEN,
    EVICTION_DEMAND_APPLIED,
    ROUTING_EVICTION_EXECUTE_FAILED,
)


@event_factory
def EvictionCooldownBlocked(
    model_id: str,
    gateway_id: str,
    evicted_model_id: str,
    escape_reason: str,
    timestamp: float,
    request_id: str | None = None,
    cooldown_remaining_s: float | None = None,
    candidates_in_cooldown: int = 0,
    candidates_demand_protected: int = 0,
) -> Event:
    """Emit when eviction uses escape hatch because all candidates were protected."""
    return Event(
        signal=EVICTION_COOLDOWN_BLOCKED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicted_model_id": evicted_model_id,
            "escape_reason": escape_reason,
            "cooldown_remaining_s": cooldown_remaining_s,
            "candidates_in_cooldown": candidates_in_cooldown,
            "candidates_demand_protected": candidates_demand_protected,
            "timestamp": timestamp,
        },
    )


@event_factory
def EvictionCooldownApplied(
    model_id: str,
    gateway_id: str,
    protected_count: int,
    cooldown_s: float,
    timestamp: float,
) -> Event:
    """Emit when cooldown protection filtered one or more eviction candidates."""
    return Event(
        signal=EVICTION_COOLDOWN_APPLIED,
        payload={
            "model_id": model_id,
            "gateway_id": gateway_id,
            "protected_count": protected_count,
            "cooldown_s": cooldown_s,
            "timestamp": timestamp,
        },
    )


@event_factory
def EvictionDemandApplied(
    model_id: str,
    gateway_id: str,
    protected_count: int,
    waiter_counts: dict[str, int],
    timestamp: float,
) -> Event:
    """Emit when demand protection filtered one or more eviction candidates."""
    return Event(
        signal=EVICTION_DEMAND_APPLIED,
        payload={
            "model_id": model_id,
            "gateway_id": gateway_id,
            "protected_count": protected_count,
            "waiter_counts": waiter_counts,
            "timestamp": timestamp,
        },
    )


@event_factory
def EvictionCooldownOverridden(
    model: str,
    node: str,
    remaining_s: float,
    requester: str,
    gateway_id: str,
    timestamp: float,
) -> Event:
    """Emit when required eviction overrides cooldown for the selected victim."""
    return Event(
        signal=EVICTION_COOLDOWN_OVERRIDDEN,
        payload={
            "model": model,
            "node": node,
            "remaining_s": remaining_s,
            "requester": requester,
            "gateway_id": gateway_id,
            "timestamp": timestamp,
        },
    )


@event_factory
def RoutingEvictionExecuteFailed(
    *,
    request_id: str,
    model_id: str,
    gateway_id: str,
    selection_tier: str,
    selection_reason: str,
    models_to_evict: list[str],
    freed_vram_mb: int,
    freed_ram_mb: int,
    estimated_cost: float,
    cooldown_protected_count: int,
    demand_protected_count: int,
    candidate_breakdown: list[dict],
    timestamp: float,
) -> Event:
    """Emit when T2 finalize-time eviction execution failed."""
    return Event(
        signal=ROUTING_EVICTION_EXECUTE_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "selection_tier": selection_tier,
            "selection_reason": selection_reason,
            "models_to_evict": models_to_evict,
            "freed_vram_mb": freed_vram_mb,
            "freed_ram_mb": freed_ram_mb,
            "estimated_cost": estimated_cost,
            "cooldown_protected_count": cooldown_protected_count,
            "demand_protected_count": demand_protected_count,
            "candidate_breakdown": candidate_breakdown,
            "timestamp": timestamp,
        },
    )
