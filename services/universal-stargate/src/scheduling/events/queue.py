# ruff: noqa: N802
"""Capacity queue event signals.

Covers request entry, wake-up, and TOCTOU race detection
in the master capacity queue. Also covers CapacityPool FIFO queue
lifecycle for per-model admission.

Master queue signals:
    queue.master.entered — request started waiting for system-wide capacity
    queue.master.woken — waiter released when capacity became available
    queue.master.timed.out — safety net timeout exceeded in queue
    queue.master.toctou — capacity check failed after wake (race detected)

Capacity pool signals:
    capacity.pool.queued — request entered per-model FIFO admission queue
    capacity.pool.waiting — request remains queued; non-terminal heartbeat
    capacity.pool.admitted — queued request assigned a slot
    capacity.pool.full — queue at max depth, request rejected immediately
    capacity.pool.cancelled — queued request removed before admission
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


# ========================================
# CapacityPool FIFO Queue Signals
# ========================================

CAPACITY_POOL_QUEUED = "capacity.pool.queued"
CAPACITY_POOL_WAITING = "capacity.pool.waiting"
CAPACITY_POOL_ADMITTED = "capacity.pool.admitted"
CAPACITY_POOL_FULL = "capacity.pool.full"


@event_factory
def CapacityPoolQueued(
    request_id: str,
    model_id: str,
    queue_position: int,
    allowed_gateways: int,
) -> Event:
    """Request entered per-model FIFO admission queue in CapacityPool."""
    return Event(
        signal=CAPACITY_POOL_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "queue_position": queue_position,
            "allowed_gateways": allowed_gateways,
        },
    )


@event_factory
def CapacityPoolWaiting(
    request_id: str,
    model_id: str,
    wait_ms: float,
    queue_position: int,
    queue_depth: int,
) -> Event:
    """Request is still queued in CapacityPool; waiting remains non-terminal."""
    return Event(
        signal=CAPACITY_POOL_WAITING,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "wait_ms": wait_ms,
            "queue_position": queue_position,
            "queue_depth": queue_depth,
        },
    )


@event_factory
def CapacityPoolAdmitted(
    request_id: str,
    model_id: str,
    gateway_id: str,
    wait_ms: float,
) -> Event:
    """Queued request assigned a slot after waiting in FIFO queue."""
    return Event(
        signal=CAPACITY_POOL_ADMITTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "wait_ms": wait_ms,
        },
    )


@event_factory
def CapacityPoolFull(
    request_id: str,
    model_id: str,
    current_depth: int,
    max_depth: int,
) -> Event:
    """Queue at max depth — request rejected immediately (overload protection)."""
    return Event(
        signal=CAPACITY_POOL_FULL,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "current_depth": current_depth,
            "max_depth": max_depth,
        },
    )


CAPACITY_POOL_CANCELLED = "capacity.pool.cancelled"


@event_factory
def CapacityPoolCancelled(
    request_id: str,
    model_id: str,
    wait_ms: float,
    reason: str,
) -> Event:
    """Queued request removed before admission due to explicit cancellation."""
    return Event(
        signal=CAPACITY_POOL_CANCELLED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "wait_ms": wait_ms,
            "reason": reason,
        },
    )


# ========================================
# Admission Pause (Starvation Preemption) Signals
# ========================================

CAPACITY_ADMISSION_PAUSED = "capacity.admission.paused"
"""
Admission for a model suspended to allow competing starved model to load.

Emitted by CapacityPool.pause_admission when the scheduler uses admission
drain as a preemption primitive against a continuously-busy model whose
eviction has been blocked for > starvation_drain_threshold_s.

Payload: {
    "model_id": str,       # routing_key of the paused model
    "duration_s": float,   # requested pause duration
    "reason": str,         # typically "starvation_relie"
}
"""

CAPACITY_ADMISSION_RESUMED = "capacity.admission.resumed"
"""
Admission pause released; queued waiters for the model may now be admitted.

Emitted by CapacityPool when either the TTL elapses, an explicit
resume_admission() call fires, or lazy expiration is detected inside
_try_immediate/_dispatch.

Payload: {
    "model_id": str,       # routing_key of the resumed model
    "reason": str,         # "ttl_expired" | "explicit" | "expired_lazy"
}
"""


@event_factory
def CapacityAdmissionPaused(
    model_id: str,
    duration_s: float,
    reason: str,
) -> Event:
    """Admission for model_id suspended for duration_s seconds."""
    return Event(
        signal=CAPACITY_ADMISSION_PAUSED,
        payload={
            "model_id": model_id,
            "duration_s": duration_s,
            "reason": reason,
        },
    )


@event_factory
def CapacityAdmissionResumed(
    model_id: str,
    reason: str,
) -> Event:
    """Admission pause cleared for model_id; queued waiters may be admitted."""
    return Event(
        signal=CAPACITY_ADMISSION_RESUMED,
        payload={
            "model_id": model_id,
            "reason": reason,
        },
    )
