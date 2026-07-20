"""Compute-capacity queue telemetry event signals and factories.

Emitted when requests wait for or acquire CPU/hybrid/GPU capacity slots.
Surfaces orchestration drift between Stargate capacity view and gateway limits.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from universal_event_bus import Event, event_factory

# ========== Compute Capacity Telemetry Event Signals ==========

COMPUTE_CAPACITY_QUEUE_WAIT = "compute.capacity.queue.wait"
"""
Emitted when a request must queue because compute capacity is at limit.

Signals orchestration drift - Stargate's view was out of sync with Gateway.

Payload:
    request_id: str - Request being queued
    model_id: str - Model the request is for
    compute_type: str - "cpu", "hybrid", or "gpu"
    queue_position: int - Queue position at enqueue time (1 = first in line)
    active_count: int - Current active requests
    limit: int - Capacity limit
    timestamp_ms: int - Milliseconds since epoch
"""

COMPUTE_CAPACITY_QUEUE_ACQUIRED = "compute.capacity.queue.acquired"
"""
Emitted when a request acquires a compute slot after waiting in queue.

Payload:
    request_id: str - Request that acquired slot
    model_id: str - Model the request is for
    compute_type: str - "cpu", "hybrid", or "gpu"
    wait_duration_ms: float - Time spent waiting in queue
    queue_position_at_enqueue: int - Position when enqueued (for correlation)
    timestamp_ms: int - Milliseconds since epoch
"""


# Compute Capacity Telemetry Event Factories
@event_factory
def ComputeCapacityQueueWait(
    request_id: str,
    model_id: str,
    compute_type: str,
    queue_position: int,
    active_count: int,
    limit: int,
    timestamp_ms: int,
) -> Event:
    """
    Create COMPUTE_CAPACITY_QUEUE_WAIT event.

    Emitted when request enters queue due to capacity limit.

    Args:
        request_id: Request being queued
        model_id: Model the request is for
        compute_type: "cpu", "hybrid", or "gpu"
        queue_position: Queue position at enqueue time (1 = first in line)
        active_count: Current active requests
        limit: Capacity limit
        timestamp_ms: Milliseconds since epoch

    Returns:
        Event with ComputeCapacityQueueWait signal
    """
    return Event(
        signal=COMPUTE_CAPACITY_QUEUE_WAIT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "queue_position": queue_position,
            "active_count": active_count,
            "limit": limit,
            "timestamp_ms": timestamp_ms,
        },
    )


@event_factory
def ComputeCapacityQueueAcquired(
    request_id: str,
    model_id: str,
    compute_type: str,
    wait_duration_ms: float,
    queue_position_at_enqueue: int,
    timestamp_ms: int,
) -> Event:
    """
    Create COMPUTE_CAPACITY_QUEUE_ACQUIRED event.

    Emitted when request acquires slot after waiting.

    Args:
        request_id: Request that acquired slot
        model_id: Model the request is for
        compute_type: "cpu", "hybrid", or "gpu"
        wait_duration_ms: Time spent waiting in queue
        queue_position_at_enqueue: Position when enqueued (for correlation)
        timestamp_ms: Milliseconds since epoch

    Returns:
        Event with ComputeCapacityQueueAcquired signal
    """
    return Event(
        signal=COMPUTE_CAPACITY_QUEUE_ACQUIRED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "wait_duration_ms": wait_duration_ms,
            "queue_position_at_enqueue": queue_position_at_enqueue,
            "timestamp_ms": timestamp_ms,
        },
    )
