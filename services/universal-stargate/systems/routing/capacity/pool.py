"""
Shim re-exporting CapacityPool symbols from the _pool package.

Preserves the historical ``systems.routing.capacity.pool`` import path.
"""

from ._pool import CapacityAcquireTimeoutError, CapacityPool, CapacityToken, QueueFullError

__all__ = [
    "CapacityAcquireTimeoutError",
    "CapacityPool",
    "CapacityToken",
    "QueueFullError",
]
