"""Shim: re-exports from the _pool package for backward compatibility."""

from ._pool import CapacityPool, CapacityToken, QueueFullError

__all__ = ["CapacityPool", "CapacityToken", "QueueFullError"]
