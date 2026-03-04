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

# Routing decisions
FEDERATION_ROUTING_DELEGATED = "federation.routing.delegated"
FEDERATION_ROUTING_ROUTED_LOCAL = "federation.routing.routed.local"
FEDERATION_ROUTING_REJECTED = "federation.routing.rejected"


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
