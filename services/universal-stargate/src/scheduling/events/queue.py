"""Master capacity queue event signals.

Covers request entry, wake-up, timeout, and TOCTOU race detection
in the master capacity queue.

Signals:
    queue.master.entered — request started waiting for system-wide capacity
    queue.master.woken — waiter released when capacity became available
    queue.master.timed.out — safety net timeout exceeded in queue
    queue.master.toctou — capacity check failed after wake (race detected)
"""

from universal_event_bus import Event, event_factory

# ========================================
# Master Queue Event Signals
# ========================================

QUEUE_MASTER_ENTERED = "queue.master.entered"
"""
Request entered master capacity queue.
Emitted when a request starts waiting for system-wide capacity.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "queue_position": int,
}
"""

QUEUE_MASTER_WOKEN = "queue.master.woken"
"""
Request woken from master capacity queue.
Emitted when capacity becomes available and a waiter is released.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "wait_time_ms": float,
    "gateway_id": str | None,  # Gateway with capacity
}
"""

QUEUE_MASTER_TIMEOUT = "queue.master.timed.out"
"""
Request timed out in master capacity queue.
Emitted when safety net timeout is exceeded.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "timeout_seconds": float,
}
"""

QUEUE_MASTER_TOCTOU = "queue.master.toctou"
"""
TOCTOU race detected after master queue wake.
Emitted when request fails capacity check after being woken.

Payload: {
    "request_id": str,
    "model_id": str,
    "compute_type": str,
    "endpoint_category": str,
    "retry_count": int,
}
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def QueueMasterEntered(
    request_id: str,
    model_id: str,
    queue_position: int,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_ENTERED event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        queue_position: Position in queue (1-indexed)
        compute_type: Optional; "cpu", "hybrid", or "gpu" (legacy)
        endpoint_category: Optional; "generation" or "embedding" (legacy)

    Returns:
        Event with QueueMasterEntered signal
    """
    return Event(
        signal=QUEUE_MASTER_ENTERED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "queue_position": queue_position,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterWoken(
    request_id: str,
    model_id: str,
    wait_time_ms: float,
    gateway_id: str | None = None,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_WOKEN event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        wait_time_ms: Time spent waiting in queue
        gateway_id: Gateway with available capacity
        compute_type: Optional (legacy)
        endpoint_category: Optional (legacy)

    Returns:
        Event with QueueMasterWoken signal
    """
    return Event(
        signal=QUEUE_MASTER_WOKEN,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "wait_time_ms": wait_time_ms,
            "gateway_id": gateway_id,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterTimedOut(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    compute_type: str = "",
    endpoint_category: str = "",
) -> Event:
    """
    Create QUEUE_MASTER_TIMEOUT event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        timeout_seconds: Timeout value that was exceeded
        compute_type: Optional (legacy)
        endpoint_category: Optional (legacy)

    Returns:
        Event with QueueMasterTimedOut signal
    """
    return Event(
        signal=QUEUE_MASTER_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "timeout_seconds": timeout_seconds,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
        },
    )


@event_factory
def QueueMasterToctou(
    request_id: str,
    model_id: str,
    compute_type: str,
    endpoint_category: str,
    retry_count: int,
) -> Event:
    """
    Create QUEUE_MASTER_TOCTOU event.

    Args:
        request_id: Request identifier
        model_id: Model being requested
        compute_type: "cpu", "hybrid", or "gpu"
        endpoint_category: "generation" or "embedding"
        retry_count: Number of retries so far

    Returns:
        Event with QueueMasterToctou signal
    """
    return Event(
        signal=QUEUE_MASTER_TOCTOU,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "compute_type": compute_type,
            "endpoint_category": endpoint_category,
            "retry_count": retry_count,
        },
    )
