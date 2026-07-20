"""Routing failure diagnostic event signals.

Package-shadow of the former ``routing_failures.py`` module. Covers resource-data
gaps, infeasibility, eviction blocks, upstream exclusion, capacity divergence,
overflow, and capacity slot leak recovery."""

# ruff: noqa: N802

from .factories import (
    CapacitySlotLeakRecovered,
    RoutingCapacityDivergence,
    RoutingCapacityPreseeded,
    RoutingEvictionBlockedBusy,
    RoutingEvictionInsufficientPermanent,
    RoutingModelInfeasible,
    RoutingOverflowFailed,
    RoutingOverflowTriggered,
    RoutingResourceDataMissing,
    RoutingUpstreamAllExcluded,
)
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

__all__ = [
    "ROUTING_RESOURCE_DATA_MISSING",
    "ROUTING_MODEL_INFEASIBLE",
    "ROUTING_EVICTION_BLOCKED_BUSY",
    "ROUTING_EVICTION_INSUFFICIENT_PERMANENT",
    "ROUTING_UPSTREAM_ALL_EXCLUDED",
    "ROUTING_CAPACITY_DIVERGENCE",
    "ROUTING_CAPACITY_PRESEEDED",
    "ROUTING_OVERFLOW_TRIGGERED",
    "ROUTING_OVERFLOW_FAILED",
    "CAPACITY_SLOT_LEAK_RECOVERED",
    "RoutingResourceDataMissing",
    "RoutingModelInfeasible",
    "RoutingEvictionBlockedBusy",
    "RoutingEvictionInsufficientPermanent",
    "RoutingUpstreamAllExcluded",
    "RoutingCapacityDivergence",
    "RoutingCapacityPreseeded",
    "RoutingOverflowTriggered",
    "RoutingOverflowFailed",
    "CapacitySlotLeakRecovered",
]
