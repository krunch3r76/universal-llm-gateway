"""Federation model load orchestration and catalog event signals.

Covers federated model load requests/confirmations/failures, orchestrator
decisions (route/load/queue/reject/evict), and catalog change detection
(model list changes and VRAM drift vs catalog estimates).

Also contains federation variants of gateway.resource.updated and
model.loaded/unloaded that use gateway_id instead of url — these reuse
the same signal strings but carry different payload shapes suitable for
the federation control plane.

Signals:
    federation.catalog.changed — federated gateway catalog changed
    federation.catalog.vram.drift — measured VRAM diverges from catalog estimate
    federation.load.requested — Master requested remote load a model
    federation.load.confirmed — remote confirmed model loaded
    federation.load.failed — remote failed to load model
    federation.orchestrator.decided — orchestrator routing/load decision
    federation.orchestrator.evicted — orchestrator evicted model from remote
    gateway.resource.updated (federation variant) — minimal wake-up signal
    model.loaded (federation variant) — gateway_id payload for federation
    model.unloaded (federation variant) — gateway_id payload for federation
"""

from typing import Any

from model_id import ModelId
from universal_event_bus import Event, event_factory

from .gateway import GATEWAY_RESOURCE_UPDATE
from .model_lifecycle import MODEL_LOADED, MODEL_UNLOADED

# ========================================
# Federation Load & Catalog Event Signals
# ========================================

FEDERATION_GATEWAY_CATALOG_CHANGED = "federation.catalog.changed"
"""
Federated gateway catalog changed
Emitted when a federated gateway's model catalog changes.
Payload: {
    "gateway_id": str,  # Unique identifier (e.g., "edge-localhost-gateway")
    "old_model_count": int,
    "new_model_count": int,
    "event_type": str | None,  # Optional: 'added', 'removed', 'changed'
    "models": list[str] | None,  # Optional: affected model IDs
}
"""

FEDERATION_CATALOG_VRAM_DRIFT = "federation.catalog.vram.drift"
"""
VRAM drift detected: measured GPU VRAM diverges from catalog estimate by >5%.
Emitted on RESOURCE_UPDATE when model_vram shows significant discrepancy.
Payload: {
    "gateway_id": str,    # Gateway where drift was observed
    "model_id": str,      # Model with drifted VRAM
    "measured_mb": int,   # Measured VRAM from nvidia-smi via RESOURCE_UPDATE
    "catalog_mb": int,    # Catalog estimate from model registry
    "drift_pct": float,   # |measured - catalog| / catalog * 100
}
"""

# Model load orchestration
FEDERATION_LOAD_REQUESTED = "federation.load.requested"
FEDERATION_LOAD_CONFIRMED = "federation.load.confirmed"
FEDERATION_LOAD_FAILED = "federation.load.failed"

# Orchestrator decisions
FEDERATION_ORCHESTRATOR_DECIDED = "federation.orchestrator.decided"
FEDERATION_ORCHESTRATOR_EVICTED = "federation.orchestrator.evicted"


# ========================================
# Factory Functions
# ========================================


@event_factory
def FederationGatewayCatalogChanged(
    gateway_id: str,
    old_model_count: int,
    new_model_count: int,
    event_type: str | None = None,
    models: list[str] | None = None,
) -> Event:
    """
    Create FEDERATION_GATEWAY_CATALOG_CHANGED event.

    Emitted when a federated gateway's model catalog changes.

    Args:
        gateway_id: Unique gateway identifier (e.g., "edge-localhost-gateway")
        old_model_count: Previous number of models
        new_model_count: New number of models
        event_type: Type of change ('added', 'removed', 'changed')
        models: List of affected model IDs

    Returns:
        Event with FEDERATION_GATEWAY_CATALOG_CHANGED signal

    Note:
        Gateway identification uses gateway_id, not URL. Master routes via
        Edge Stargate URL, never direct to Gateway.
    """
    payload: dict[str, Any] = {
        "gateway_id": gateway_id,
        "old_model_count": old_model_count,
        "new_model_count": new_model_count,
    }
    if event_type is not None:
        payload["event_type"] = event_type
    if models is not None:
        payload["models"] = models
    return Event(
        signal=FEDERATION_GATEWAY_CATALOG_CHANGED,
        payload=payload,
    )


@event_factory
def FederationCatalogVramDrift(  # noqa: N802
    gateway_id: str,
    model_id: str,
    measured_mb: int,
    catalog_mb: int,
    drift_pct: float,
) -> Event:
    """
    Create FEDERATION_CATALOG_VRAM_DRIFT event.

    Args:
        gateway_id: Gateway where drift was observed
        model_id: Model with drifted VRAM
        measured_mb: Measured VRAM from runtime telemetry
        catalog_mb: Catalog-estimated VRAM
        drift_pct: Percent drift between measured and catalog values

    Returns:
        Event with FederationCatalogVramDrift signal
    """
    return Event(
        signal=FEDERATION_CATALOG_VRAM_DRIFT,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
            "measured_mb": measured_mb,
            "catalog_mb": catalog_mb,
            "drift_pct": round(drift_pct, 2),
        },
    )


@event_factory
def FederationLoadRequested(
    request_id: str,
    target_remote: str,
    model_id: str,
) -> Event:
    """
    Master requested a remote Stargate to load a model.

    Args:
        request_id: Request identifier
        target_remote: Target remote Stargate identifier
        model_id: Requested model identifier

    Returns:
        Event with FederationLoadRequested signal
    """
    return Event(
        signal=FEDERATION_LOAD_REQUESTED,
        payload={
            "request_id": request_id,
            "target_remote": target_remote,
            "model_id": model_id,
        },
    )


@event_factory
def FederationLoadConfirmed(
    request_id: str,
    remote_id: str,
    model_id: str,
    duration_ms: int,
) -> Event:
    """
    Remote confirmed model load completion.

    Args:
        request_id: Request identifier
        remote_id: Remote Stargate identifier
        model_id: Loaded model identifier
        duration_ms: End-to-end load duration in milliseconds

    Returns:
        Event with FederationLoadConfirmed signal
    """
    return Event(
        signal=FEDERATION_LOAD_CONFIRMED,
        payload={
            "request_id": request_id,
            "remote_id": remote_id,
            "model_id": model_id,
            "duration_ms": duration_ms,
        },
    )


@event_factory
def FederationLoadFailed(
    request_id: str,
    remote_id: str,
    model_id: str,
    error: str,
) -> Event:
    """
    Remote failed to load model.

    Args:
        request_id: Request identifier
        remote_id: Remote Stargate identifier
        model_id: Model that failed to load
        error: Error message from the remote

    Returns:
        Event with FederationLoadFailed signal
    """
    return Event(
        signal=FEDERATION_LOAD_FAILED,
        payload={
            "request_id": request_id,
            "remote_id": remote_id,
            "model_id": model_id,
            "error": error,
        },
    )


@event_factory
def FederationOrchestratorDecided(
    request_id: str,
    decision_type: str,  # "route" | "load" | "queue" | "reject"
    target: str | None,
    reason: str,
    alternatives_considered: list[str] | None = None,
) -> Event:
    """Orchestrator made a routing/load decision."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "decision_type": decision_type,
        "target": target,
        "reason": reason,
    }
    if alternatives_considered is not None:
        payload["alternatives_considered"] = alternatives_considered
    return Event(signal=FEDERATION_ORCHESTRATOR_DECIDED, payload=payload)


@event_factory
def FederationOrchestratorEvicted(
    target_remote: str,
    model_id: str,
    reason: str,
) -> Event:
    """
    Orchestrator evicted a model from a remote.

    Args:
        target_remote: Remote Stargate identifier
        model_id: Evicted model identifier
        reason: Eviction reason

    Returns:
        Event with FederationOrchestratorEvicted signal
    """
    return Event(
        signal=FEDERATION_ORCHESTRATOR_EVICTED,
        payload={
            "target_remote": target_remote,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationGatewayResourceUpdateSignal(
    gateway_id: str,
    source: str = "http_polling",
) -> Event:
    """
    Create GATEWAY_RESOURCE_UPDATE wake-up signal for federation.

    Minimal payload for telemetry freshness notification.
    Used by HTTP polling master to wake up FreshnessWaiter.

    Args:
        gateway_id: Gateway identifier
        source: Source of update ("http_polling", "websocket", etc.)

    Returns:
        Event with GatewayResourceUpdate signal
    """
    return Event(
        signal=GATEWAY_RESOURCE_UPDATE,
        payload={
            "gateway_id": gateway_id,
            "source": source,
        },
    )


@event_factory
def FederationModelLoaded(gateway_id: str, model_id: ModelId | str) -> Event:
    """
    Create MODEL_LOADED event for federation (gateway_id instead of url).

    Args:
        gateway_id: Gateway identifier
        model_id: Model that was loaded

    Returns:
        Event with ModelLoaded signal
    """
    return Event(
        signal=MODEL_LOADED,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
        },
    )


@event_factory
def FederationModelUnloaded(gateway_id: str, model_id: ModelId | str) -> Event:
    """
    Create MODEL_UNLOADED event for federation (gateway_id instead of url).

    Args:
        gateway_id: Gateway identifier
        model_id: Model that was unloaded

    Returns:
        Event with ModelUnloaded signal
    """
    return Event(
        signal=MODEL_UNLOADED,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
        },
    )
