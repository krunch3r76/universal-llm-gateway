"""
Universal concurrency primitives.

Provides FIFO-fair capacity gating and waiter management as semaphore
replacements. Use composition to add service-specific policy.

Primitives:
    FifoWaitQueue: FIFO waiter list for event-driven wake patterns
    CapacityCounter: Counter only, callback on release (compose with FifoWaitQueue)
    FifoCapacityGate: FIFO capacity gate (semaphore replacement)

Design principles:
    - No silent defaults (caller must provide configuration)
    - Fail loudly on invariant violations
    - O(1) hot-path operations (enqueue, wake)
    - Composition over inheritance
"""

from .capacity_counter import CapacityCounter
from .exceptions import (
    CapacityLimitError,
    CrossLaneTransferError,
    OverReleaseError,
    TransferHolderError,
)
from .fifo_capacity_gate import FifoCapacityGate
from .fifo_wait_queue import FifoWaitQueue
from .queue_utils import QUEUE_EMPTY_ERRORS, drain_queue_batch
from .types import CounterStats, GateStats, WaitQueueStats

__all__ = [
    "CapacityCounter",
    "CapacityLimitError",
    "CrossLaneTransferError",
    "CounterStats",
    "drain_queue_batch",
    "FifoCapacityGate",
    "FifoWaitQueue",
    "GateStats",
    "OverReleaseError",
    "QUEUE_EMPTY_ERRORS",
    "TransferHolderError",
    "WaitQueueStats",
]
