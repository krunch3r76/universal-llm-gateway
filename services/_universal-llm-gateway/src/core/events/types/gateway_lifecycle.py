"""Gateway lifecycle and VRAM reconciler event signals and factories.

Includes shutdown/drain coordination plus orphan/staleness/phantom/ghost
VRAM reconciliation signals. Consumed by shutdown sequence and VramReconciler.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from universal_event_bus import Event, event_factory

# ========== Gateway Lifecycle Event Signals ==========

GATEWAY_SHUTDOWN = "gateway.shutdown"
"""
Emitted when the gateway is shutting down immediately.

Subscribers should NOT expect in-flight requests to complete.
Stargate uses this to retry/reroute requests to other gateways.

Payload:
    gateway_id: str - Identifier of the gateway shutting down
    reason: str - Reason for shutdown (e.g., "signal", "requested")
    timestamp: float - Unix timestamp of shutdown initiation
"""

GATEWAY_DRAINING = "gateway.draining"
"""
Emitted when the gateway begins graceful shutdown (draining mode).

Subscribers MAY expect in-flight requests to complete within the drain timeout.
New requests should be routed to other gateways.

Payload:
    gateway_id: str - Identifier of the gateway draining
    reason: str - Reason for shutdown
    timeout: float - Seconds until forced shutdown
    timestamp: float - Unix timestamp of drain initiation
"""

VRAM_ORPHAN_DETECTED = "gateway.vram.orphan.detected"
"""
Emitted when hardware VRAM exceeds tracked model VRAM by > threshold.
Indicates unmanaged GPU processes outside the model lifecycle.

Payload:
    hardware_used_mb: int - VRAM used per pynvml
    catalog_used_mb: int - VRAM tracked by resource tracker (measured when available)
    discrepancy_mb: int - positive delta (hardware - catalog)
    tracked_models: list[str] - currently tracked model IDs
"""

VRAM_STALENESS_DETECTED = "gateway.vram.staleness.detected"
"""
Emitted when tracked model VRAM exceeds hardware VRAM by > threshold.
Indicates catalog values are stale — tracked models not using claimed VRAM.

Payload:
    hardware_used_mb: int - VRAM used per pynvml
    catalog_used_mb: int - VRAM tracked by resource tracker (measured when available)
    discrepancy_mb: int - negative delta (hardware - catalog)
    tracked_models: list[str] - currently tracked model IDs
"""

PHANTOM_MODEL_DETECTED = "gateway.model.phantom.detected"
"""
Emitted when a running worker process is not tracked as LOADED/BUSY.

Payload:
    model_id: str - Model ID of phantom process
    process_status: str - Runtime process status (e.g. "running")
    tracker_status: str | None - Current ResourceTracker status if present
"""

PHANTOM_MODEL_CLEANED = "gateway.model.phantom.cleaned"
"""
Emitted after phantom cleanup attempt.

Payload:
    model_id: str - Model ID of phantom process
    success: bool - Whether cleanup succeeded
    vram_freed_mb: int | None - Estimated VRAM reclaimed by cleanup
"""

GHOST_MODEL_CLEANED = "gateway.model.ghost.cleaned"
"""
Emitted after ghost model cleanup (tracked as loaded but engine dead).

Payload:
    model_id: str - Model ID of ghost process
    success: bool - Whether cleanup succeeded
    vram_freed_mb: int | None - Estimated VRAM reclaimed by cleanup
"""


# Gateway Lifecycle Event Factories
@event_factory
def GatewayShutdown(
    gateway_id: str,
    reason: str,
    timestamp: float,
) -> Event:
    """
    Create GATEWAY_SHUTDOWN event.

    Subscribers should NOT expect in-flight requests to complete.

    Args:
        gateway_id: Identifier of gateway shutting down
        reason: Reason for shutdown (e.g., "signal", "requested")
        timestamp: Unix timestamp of shutdown initiation

    Returns:
        Event with GatewayShutdown signal
    """
    return Event(
        signal=GATEWAY_SHUTDOWN,
        payload={
            "gateway_id": gateway_id,
            "reason": reason,
            "timestamp": timestamp,
        },
    )


@event_factory
def GatewayDraining(
    gateway_id: str,
    reason: str,
    timeout: float,
    timestamp: float,
) -> Event:
    """
    Create GATEWAY_DRAINING event.

    Subscribers MAY expect in-flight requests to complete within timeout.

    Args:
        gateway_id: Identifier of gateway draining
        reason: Reason for shutdown
        timeout: Seconds until forced shutdown
        timestamp: Unix timestamp of drain initiation

    Returns:
        Event with GatewayDraining signal
    """
    return Event(
        signal=GATEWAY_DRAINING,
        payload={
            "gateway_id": gateway_id,
            "reason": reason,
            "timeout": timeout,
            "timestamp": timestamp,
        },
    )


@event_factory
def VramOrphanDetected(
    hardware_used_mb: int,
    catalog_used_mb: int,
    discrepancy_mb: int,
    tracked_models: list[str],
) -> Event:
    """Emitted when hardware VRAM exceeds catalog — unmanaged GPU processes suspected."""
    return Event(
        signal=VRAM_ORPHAN_DETECTED,
        payload={
            "hardware_used_mb": hardware_used_mb,
            "catalog_used_mb": catalog_used_mb,
            "discrepancy_mb": discrepancy_mb,
            "tracked_models": tracked_models,
        },
    )


@event_factory
def VramStalenessDetected(
    hardware_used_mb: int,
    catalog_used_mb: int,
    discrepancy_mb: int,
    tracked_models: list[str],
) -> Event:
    """Emitted when catalog VRAM exceeds hardware — catalog profiles stale."""
    return Event(
        signal=VRAM_STALENESS_DETECTED,
        payload={
            "hardware_used_mb": hardware_used_mb,
            "catalog_used_mb": catalog_used_mb,
            "discrepancy_mb": discrepancy_mb,
            "tracked_models": tracked_models,
        },
    )


@event_factory
def PhantomModelDetected(
    model_id: str,
    process_status: str,
    tracker_status: str | None = None,
) -> Event:
    """Create PHANTOM_MODEL_DETECTED event."""
    return Event(
        signal=PHANTOM_MODEL_DETECTED,
        payload={
            "model_id": model_id,
            "process_status": process_status,
            "tracker_status": tracker_status,
        },
    )


@event_factory
def PhantomModelCleaned(
    model_id: str,
    success: bool,
    vram_freed_mb: int | None = None,
) -> Event:
    """Create PHANTOM_MODEL_CLEANED event."""
    return Event(
        signal=PHANTOM_MODEL_CLEANED,
        payload={
            "model_id": model_id,
            "success": success,
            "vram_freed_mb": vram_freed_mb,
        },
    )


@event_factory
def GhostModelCleaned(
    model_id: str,
    success: bool,
    vram_freed_mb: int | None = None,
) -> Event:
    """Create GHOST_MODEL_CLEANED event (tracked model whose engine was dead)."""
    return Event(
        signal=GHOST_MODEL_CLEANED,
        payload={
            "model_id": model_id,
            "success": success,
            "vram_freed_mb": vram_freed_mb,
        },
    )
