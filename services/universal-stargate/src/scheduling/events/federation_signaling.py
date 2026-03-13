"""Federation connection, telemetry, and routing signal events.

Covers the control-plane signaling between Master and remote Edge/Relay nodes:
authentication, telemetry flow, and routing delegation decisions.

Signals:
    federation.connection.established — remote Stargate connected
    federation.connection.lost — remote Stargate disconnected
    federation.connection.authenticated — remote authenticated
    federation.telemetry.received — Master received telemetry from remote
    federation.telemetry.marked.stale — telemetry exceeded staleness threshold
    federation.telemetry.applied — telemetry applied to Master state
    federation.routing.delegated — Master delegated request to remote
    federation.routing.routed.local — Master routed request locally
    federation.routing.rejected — Master rejected request (no target)
"""

# ruff: noqa: N802

from typing import Any

from universal_event_bus import Event, event_factory

# ========================================
# Federation Signaling Event Signals
# ========================================

# Connection lifecycle
FEDERATION_CONNECTION_ESTABLISHED = "federation.connection.established"
FEDERATION_CONNECTION_LOST = "federation.connection.lost"
FEDERATION_CONNECTION_AUTHENTICATED = "federation.connection.authenticated"

# Telemetry flow
FEDERATION_TELEMETRY_RECEIVED = "federation.telemetry.received"
FEDERATION_TELEMETRY_MARKED_STALE = "federation.telemetry.marked.stale"
FEDERATION_TELEMETRY_APPLIED = "federation.telemetry.applied"
FEDERATION_TELEMETRY_WIRED = "federation.telemetry.wired"

# Routing decisions
FEDERATION_ROUTING_DELEGATED = "federation.routing.delegated"
FEDERATION_ROUTING_ROUTED_LOCAL = "federation.routing.routed.local"
FEDERATION_ROUTING_REJECTED = "federation.routing.rejected"

# Peer auth/lifecycle
FEDERATION_PEER_AUTH_FAILED = "federation.peer.auth_failed"
FEDERATION_PEER_DISCONNECTED = "federation.peer.disconnected"

# Request forwarding / telemetry reconciliation
FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED = (
    "federation.request.inference_started.forwarded"
)
FEDERATION_MODEL_LIFECYCLE_EVENT = "federation.model.lifecycle_event"
FEDERATION_RESOURCE_UPDATED = "federation.resource.updated"
FEDERATED_GATEWAY_REMOVED = "federated_gateway.removed"

# VRAM measurement orchestration (edge <-> master)
FEDERATION_VRAM_REQUEST_SENT = "federation.vram_request.sent"
FEDERATION_VRAM_REQUEST_FAILED = "federation.vram_request.failed"
FEDERATION_VRAM_RESPONSE_RECEIVED = "federation.vram_response.received"

# Circuit-breaker request admission
FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED = (
    "federation.circuit_breaker.request_rejected"
)


# ========================================
# Factory Functions
# ========================================


@event_factory
def FederationConnectionEstablished(
    remote_id: str,
    transport: str,  # "websocket" | "http_polling"
    latency_ms: int | None = None,
) -> Event:
    """Remote Stargate connected to Master."""
    payload: dict[str, Any] = {
        "remote_id": remote_id,
        "transport": transport,
    }
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    return Event(signal=FEDERATION_CONNECTION_ESTABLISHED, payload=payload)


@event_factory
def FederationConnectionLost(
    remote_id: str,
    reason: str,
) -> Event:
    """Remote Stargate disconnected from Master."""
    return Event(
        signal=FEDERATION_CONNECTION_LOST,
        payload={"remote_id": remote_id, "reason": reason},
    )


@event_factory
def FederationConnectionAuthenticated(
    remote_id: str,
    method: str,
) -> Event:
    """Remote Stargate authenticated with Master."""
    return Event(
        signal=FEDERATION_CONNECTION_AUTHENTICATED,
        payload={"remote_id": remote_id, "method": method},
    )


@event_factory
def FederationTelemetryReceived(
    remote_id: str,
    model_count: int,
    resource_summary: dict[str, Any],
    telemetry_age_ms: int | None = None,
) -> Event:
    """Master received telemetry from Remote/Edge."""
    payload: dict[str, Any] = {
        "remote_id": remote_id,
        "model_count": model_count,
        "resource_summary": resource_summary,
    }
    if telemetry_age_ms is not None:
        payload["telemetry_age_ms"] = telemetry_age_ms
    return Event(signal=FEDERATION_TELEMETRY_RECEIVED, payload=payload)


@event_factory
def FederationTelemetryMarkedStale(
    remote_id: str,
    age_seconds: float,
    threshold_seconds: float,
) -> Event:
    """Telemetry from Remote exceeded staleness threshold."""
    return Event(
        signal=FEDERATION_TELEMETRY_MARKED_STALE,
        payload={
            "remote_id": remote_id,
            "age_seconds": age_seconds,
            "threshold_seconds": threshold_seconds,
        },
    )


@event_factory
def FederationTelemetryApplied(
    remote_id: str,
    changes: list[str],
) -> Event:
    """Telemetry applied to Master state."""
    return Event(
        signal=FEDERATION_TELEMETRY_APPLIED,
        payload={"remote_id": remote_id, "changes": changes},
    )


@event_factory
def FederationRoutingDelegated(
    request_id: str,
    target_remote: str,
    model_id: str,
    reason: str | None = None,
) -> Event:
    """Master delegated request to Remote Stargate."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "target_remote": target_remote,
        "model_id": model_id,
    }
    if reason:
        payload["reason"] = reason
    return Event(signal=FEDERATION_ROUTING_DELEGATED, payload=payload)


@event_factory
def FederationRoutingRoutedLocal(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master routed request to local Gateway."""
    return Event(
        signal=FEDERATION_ROUTING_ROUTED_LOCAL,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationRoutingRejected(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master rejected request (no available target)."""
    return Event(
        signal=FEDERATION_ROUTING_REJECTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationPeerAuthFailed(
    peer_id: str,
    reason: str,
) -> Event:
    """Inbound peer auth failed on edge federation server."""
    return Event(
        signal=FEDERATION_PEER_AUTH_FAILED,
        payload={"peer_id": peer_id, "reason": reason},
    )


@event_factory
def FederationPeerDisconnected(
    peer_id: str,
    remaining_peers: int,
) -> Event:
    """Authenticated peer disconnected from edge federation server."""
    return Event(
        signal=FEDERATION_PEER_DISCONNECTED,
        payload={"peer_id": peer_id, "remaining_peers": remaining_peers},
    )


@event_factory
def FederationTelemetryWired(
    gateway_url: str,
    gateway_id: str,
) -> Event:
    """Edge finished wiring local gateway telemetry forwarding."""
    return Event(
        signal=FEDERATION_TELEMETRY_WIRED,
        payload={"gateway_url": gateway_url, "gateway_id": gateway_id},
    )


@event_factory
def FederationRequestInferenceStartedForwarded(
    request_id: str | None,
    peer_count: int,
) -> Event:
    """Edge forwarded request.inference.started to federation peers."""
    return Event(
        signal=FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED,
        payload={"request_id": request_id, "peer_count": peer_count},
    )


@event_factory
def FederationModelLifecycleEvent(
    gateway_id: str,
    msg_type: str,
    model_id: str,
) -> Event:
    """Master applied federated model lifecycle telemetry event."""
    return Event(
        signal=FEDERATION_MODEL_LIFECYCLE_EVENT,
        payload={
            "gateway_id": gateway_id,
            "msg_type": msg_type,
            "model_id": model_id,
        },
    )


@event_factory
def FederationResourceUpdated(
    gateway_id: str,
    vram_free_mb: int,
    ram_free_mb: int,
) -> Event:
    """Master applied a RESOURCE_UPDATE from a federated gateway."""
    return Event(
        signal=FEDERATION_RESOURCE_UPDATED,
        payload={
            "gateway_id": gateway_id,
            "vram_free_mb": vram_free_mb,
            "ram_free_mb": ram_free_mb,
        },
    )


@event_factory
def FederatedGatewayRemoved(
    gateway_id: str,
    remote_id: str,
) -> Event:
    """Master removed a gateway after remote disconnect."""
    return Event(
        signal=FEDERATED_GATEWAY_REMOVED,
        payload={"gateway_id": gateway_id, "remote_id": remote_id},
    )


@event_factory
def FederationVramRequestSent(
    request_id: str,
    peer_id: str,
    device_index: int,
) -> Event:
    """Edge sent VRAM snapshot request to a connected peer."""
    return Event(
        signal=FEDERATION_VRAM_REQUEST_SENT,
        payload={
            "request_id": request_id,
            "peer_id": peer_id,
            "device_index": device_index,
        },
    )


@event_factory
def FederationVramRequestFailed(
    request_id: str,
    reason: str,
) -> Event:
    """Edge failed to dispatch VRAM request to any peer."""
    return Event(
        signal=FEDERATION_VRAM_REQUEST_FAILED,
        payload={"request_id": request_id, "reason": reason},
    )


@event_factory
def FederationVramResponseReceived(
    request_id: str,
    matched: bool,
) -> Event:
    """Edge resolved (or failed to resolve) VRAM response to pending request."""
    return Event(
        signal=FEDERATION_VRAM_RESPONSE_RECEIVED,
        payload={"request_id": request_id, "matched": matched},
    )


@event_factory
def FederationCircuitBreakerRequestRejected(
    gateway_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Circuit-breaker rejected request admission."""
    return Event(
        signal=FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED,
        payload={"gateway_id": gateway_id, "model_id": model_id, "reason": reason},
    )
