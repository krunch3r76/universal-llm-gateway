"""
Model eviction module for resource management.

Exports typed eviction execution helpers for federated unload paths.
"""

from .event_waiter import EvictionWaiter, UnloadResult
from .executor import (
    EvictionInflightRegistry,
    EvictionOutcome,
    EvictionStatus,
    execute_eviction_plan,
    get_eviction_plan_for_gateway,
)

__all__ = [
    "EvictionInflightRegistry",
    "EvictionOutcome",
    "EvictionStatus",
    "EvictionWaiter",
    "UnloadResult",
    "execute_eviction_plan",
    "get_eviction_plan_for_gateway",
]
