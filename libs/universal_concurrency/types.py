"""Type definitions for universal_concurrency primitives."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class WaitQueueStats:
    """Statistics for FifoWaitQueue."""

    queued: int
    woken_total: int
    cancelled_total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CounterStats:
    """Statistics for CapacityCounter."""

    active: int
    limit: int
    total_acquired: int
    total_released: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GateStats:
    """Statistics for FifoCapacityGate."""

    active: int
    limit: int
    queued: int
    total_acquired: int
    total_released: int
    total_timeouts: int
    total_cancellations: int
