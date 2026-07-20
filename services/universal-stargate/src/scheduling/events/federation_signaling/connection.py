"""Federation connection and peer-auth lifecycle event signals.

Established/lost/authenticated plus peer auth-failed/disconnected.
Imported via the ``federation_signaling`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

FEDERATION_CONNECTION_ESTABLISHED = "federation.connection.established"

FEDERATION_CONNECTION_LOST = "federation.connection.lost"

FEDERATION_CONNECTION_AUTHENTICATED = "federation.connection.authenticated"

FEDERATION_PEER_AUTH_FAILED = "federation.peer.auth.failed"

FEDERATION_PEER_DISCONNECTED = "federation.peer.disconnected"


@event_factory
def FederationConnectionEstablished(
    remote_id: str,
    transport: str,  # "websocket" | "http_polling"
    latency_ms: int | None = None,
) -> Event:
    """Remote Stargate connected to Master."""
    payload = {
        "remote_id": remote_id,
        "transport": transport,
        **({"latency_ms": latency_ms} if latency_ms is not None else {}),
    }
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
def FederationPeerAuthFailed(
    peer_id: str,
    reason: str,
) -> Event:
    """Record rejected peer authentication with stable reason text for forensics."""
    return Event(
        signal=FEDERATION_PEER_AUTH_FAILED,
        payload={"peer_id": peer_id, "reason": reason},
    )


@event_factory
def FederationPeerDisconnected(
    peer_id: str,
    remaining_peers: int,
) -> Event:
    """Record authenticated peer disconnect and expose remaining peer cardinality."""
    return Event(
        signal=FEDERATION_PEER_DISCONNECTED,
        payload={"peer_id": peer_id, "remaining_peers": remaining_peers},
    )
