"""Gateway state, retry, and resource event signals.

Covers gateway connectivity/health transitions, retry telemetry,
resource updates (VRAM/RAM), and resource reservation lifecycle.

Signals:
    gateway.state.changed — unified connectivity + health transition
    gateway.retry.attempted — request retry with backoff
    gateway.resource.updated — VRAM/RAM/model state snapshot
    resource.reserved — VRAM/RAM reserved for model load
    resource.released — reservation released (completed/expired/cancelled)
"""

from universal_event_bus import Event, event_factory

# ========================================
# Gateway Event Signals
# ========================================

GATEWAY_STATE_CHANGED = "gateway.state.changed"
"""
Unified gateway state changed event (Phase 2)
Consolidates connectivity and health changes into single comprehensive event.

Payload: {
    "url": str,
    "connectivity": str,  # "reachable" | "unreachable"
    "health": str,  # "healthy" | "unhealthy" | "unknown"
    "previous_connectivity": Optional[str],
    "previous_health": Optional[str],
    "transition_type": str,  # "connectivity_only" | "health_only" | "both" | "initial"
    "check_duration_ms": int
}
"""

GATEWAY_RETRY_ATTEMPTED = "gateway.retry.attempted"
"""
Gateway request retry attempted (structured telemetry)
Emitted when a gateway request fails and retry is attempted.

Payload: {
    "gateway_url": str,
    "method": str,  # HTTP method
    "path": str,
    "attempt": int,  # Current attempt number (1-indexed)
    "max_retries": int,
    "error_type": str,  # Exception class name
    "error_message": str,
    "backoff_delay_ms": int  # Milliseconds until next retry
}
"""

GATEWAY_RESOURCE_UPDATE = "gateway.resource.updated"
"""
Gateway resource information updated
Payload: {
    "url": str,
    "total_vram_mb": int,
    "available_vram_mb": int,
    "total_ram_mb": int,
    "available_ram_mb": int,
    "loaded_models": list[str],  # Set converted to list for JSON
    "busy_models": list[str]     # Set converted to list for JSON
}
"""

RESOURCE_RESERVED = "resource.reserved"
"""
Resources reserved for model loading
Emitted when resource manager reserves VRAM/RAM for a model load operation.

Payload: {
    "gateway_name": str,
    "model_id": str,
    "reservation_id": str,
    "vram_mb": int,
    "ram_mb": int,
    "timeout_seconds": float
}
"""

RESOURCE_RELEASED = "resource.released"
"""
Resources released from reservation
Emitted when resource reservation is released (completed, expired, or cancelled).

Payload: {
    "gateway_name": str,
    "model_id": str,
    "reservation_id": str,
    "vram_mb": int,
    "ram_mb": int,
    "reason": str  # "completed" | "expired" | "cancelled"
}
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def GatewayStateChanged(
    url: str,
    connectivity: str,
    health: str,
    transition_type: str,
    previous_connectivity: str | None = None,
    previous_health: str | None = None,
    check_duration_ms: int = 0,
) -> Event:
    """
    Create GATEWAY_STATE_CHANGED event.

    Args:
        url: Gateway URL
        connectivity: "reachable" | "unreachable"
        health: "healthy" | "unhealthy" | "unknown"
        transition_type: "connectivity_only" | "health_only" | "both" | "initial"
        previous_connectivity: Previous connectivity state
        previous_health: Previous health state
        check_duration_ms: Duration of health check

    Returns:
        Event with GatewayStateChanged signal
    """
    return Event(
        signal=GATEWAY_STATE_CHANGED,
        payload={
            "url": url,
            "connectivity": connectivity,
            "health": health,
            "previous_connectivity": previous_connectivity,
            "previous_health": previous_health,
            "transition_type": transition_type,
            "check_duration_ms": check_duration_ms,
        },
    )


@event_factory
def GatewayRetryAttempted(
    gateway_url: str,
    method: str,
    path: str,
    attempt: int,
    max_retries: int,
    error_type: str,
    error_message: str,
    backoff_delay_ms: int,
) -> Event:
    """
    Create GATEWAY_RETRY_ATTEMPTED event.

    Args:
        gateway_url: Gateway URL
        method: HTTP method
        path: Request path
        attempt: Current attempt number (1-indexed)
        max_retries: Maximum retry count
        error_type: Exception class name
        error_message: Error message
        backoff_delay_ms: Milliseconds until next retry

    Returns:
        Event with GatewayRetryAttempted signal
    """
    return Event(
        signal=GATEWAY_RETRY_ATTEMPTED,
        payload={
            "gateway_url": gateway_url,
            "method": method,
            "path": path,
            "attempt": attempt,
            "max_retries": max_retries,
            "error_type": error_type,
            "error_message": error_message,
            "backoff_delay_ms": backoff_delay_ms,
        },
    )


@event_factory
def GatewayResourceUpdate(
    url: str,
    total_vram_mb: int,
    available_vram_mb: int,
    total_ram_mb: int,
    available_ram_mb: int,
    loaded_models: list[str],
    busy_models: list[str],
) -> Event:
    """
    Create GATEWAY_RESOURCE_UPDATE event.

    Args:
        url: Gateway URL
        total_vram_mb: Total VRAM in MB
        available_vram_mb: Available VRAM in MB
        total_ram_mb: Total RAM in MB
        available_ram_mb: Available RAM in MB
        loaded_models: List of loaded model IDs
        busy_models: List of busy model IDs

    Returns:
        Event with GatewayResourceUpdate signal
    """
    return Event(
        signal=GATEWAY_RESOURCE_UPDATE,
        payload={
            "url": url,
            "total_vram_mb": total_vram_mb,
            "available_vram_mb": available_vram_mb,
            "total_ram_mb": total_ram_mb,
            "available_ram_mb": available_ram_mb,
            "loaded_models": loaded_models,
            "busy_models": busy_models,
        },
    )


@event_factory
def ResourceReserved(
    gateway_name: str,
    model_id: str,
    reservation_id: str,
    vram_mb: int,
    ram_mb: int,
    timeout_seconds: float,
) -> Event:
    """
    Create RESOURCE_RESERVED event.

    Args:
        gateway_name: Gateway name
        model_id: Model for reservation
        reservation_id: Unique reservation ID
        vram_mb: VRAM reserved in MB
        ram_mb: RAM reserved in MB
        timeout_seconds: Reservation timeout

    Returns:
        Event with ResourceReserved signal
    """
    return Event(
        signal=RESOURCE_RESERVED,
        payload={
            "gateway_name": gateway_name,
            "model_id": model_id,
            "reservation_id": reservation_id,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "timeout_seconds": timeout_seconds,
        },
    )


@event_factory
def ResourceReleased(
    gateway_name: str,
    model_id: str,
    reservation_id: str,
    vram_mb: int,
    ram_mb: int,
    reason: str,
) -> Event:
    """
    Create RESOURCE_RELEASED event.

    Args:
        gateway_name: Gateway name
        model_id: Model for reservation
        reservation_id: Unique reservation ID
        vram_mb: VRAM released in MB
        ram_mb: RAM released in MB
        reason: "completed" | "expired" | "cancelled"

    Returns:
        Event with ResourceReleased signal
    """
    return Event(
        signal=RESOURCE_RELEASED,
        payload={
            "gateway_name": gateway_name,
            "model_id": model_id,
            "reservation_id": reservation_id,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "reason": reason,
        },
    )
