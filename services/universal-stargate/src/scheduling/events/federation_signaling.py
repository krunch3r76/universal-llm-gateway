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
    federation.telemetry.wired — telemetry bridge wiring confirmed
    federation.activation.filtered.empty — available > 0 but activated == ∅
    federation.routing.delegated — Master delegated request to remote
    federation.routing.routed.local — Master routed request locally
    federation.routing.rejected — Master rejected request (no target)
    federation.peer.auth.failed — remote authentication failed
    federation.peer.disconnected — authenticated remote disconnected
    federation.request.inference.forwarded — inference request forwarded to peers
    federation.model.lifecycle — model lifecycle event occurred
    federation.resource.updated — resource telemetry updated
    federation.gateway.removed — federated gateway removed
    federation.vram.request.sent — VRAM probe request sent
    federation.vram.request.failed — VRAM probe request failed
    federation.vram.response.received — VRAM probe response received
    federation.circuit.breaker.rejected — request rejected by circuit breaker
    federation.gateway.degraded — gateway crossed consecutive-timeout threshold
    federation.gateway.unhealthy — gateway crossed consecutive-disconnect threshold
    federation.gateway.recovered — previously degraded or unhealthy gateway recovered
    federation.gateway.liveness.stale — passive heartbeat staleness alert (read-only)
    federation.edge.container.exited — relay-detected local edge container UDS failure
    federation.link.timeout — native WS ping/pong keepalive timed out (discriminated)
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
FEDERATION_PEER_AUTH_FAILED = "federation.peer.auth.failed"
FEDERATION_PEER_DISCONNECTED = "federation.peer.disconnected"

# Request forwarding / telemetry reconciliation
FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED = (
    "federation.request.inference.forwarded"
)
FEDERATION_MODEL_LIFECYCLE_EVENT = "federation.model.lifecycle"
FEDERATION_RESOURCE_UPDATED = "federation.resource.updated"
FEDERATED_GATEWAY_REMOVED = "federation.gateway.removed"

# VRAM measurement orchestration (edge <-> master)
FEDERATION_VRAM_REQUEST_SENT = "federation.vram.request.sent"
FEDERATION_VRAM_REQUEST_FAILED = "federation.vram.request.failed"
FEDERATION_VRAM_RESPONSE_RECEIVED = "federation.vram.response.received"

# Activation diagnostics
FEDERATION_ACTIVATION_FILTERED_EMPTY = "federation.activation.filtered.empty"

# Circuit-breaker request admission
FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED = "federation.circuit.breaker.rejected"

# Gateway-wide health: DEGRADED (timeouts; coordination only, ¬routing exclusion)
FEDERATION_GATEWAY_DEGRADED = "federation.gateway.degraded"
"""
Emitted when a federated gateway crosses the consecutive-timeout
threshold. The gateway remains routable — this is a coordination signal
for batch consumers (RAG indexing, bulk evaluation) so they can throttle
or pause submission to the affected gateway. Existing
contention/staleness scoring already biases foreground routing away from
saturated gateways.

Cleared by `federation.gateway.recovered` with `kind="degradation"` on
the first successful response from the gateway.

Payload: {
    "gateway_id": str,
    "consecutive_timeouts": int,  # count that triggered the transition
    "first_error_code": str,      # REQUEST_TIMEOUT | INFERENCE_TIMEOUT | LOAD_TIMEOUT
}
"""

# Gateway-wide health: UNHEALTHY (disconnects; routing exclusion)
FEDERATION_GATEWAY_UNHEALTHY = "federation.gateway.unhealthy"
"""
Emitted when a federated gateway crosses the consecutive-disconnect
threshold. The gateway is excluded from routing for `cooldown_s`
seconds, after which the existing HALF_OPEN probe machinery tests
recovery via a single test request.

Distinct from DEGRADED because a disconnected gateway cannot serve any
request — exclusion loses nothing, and HALF_OPEN probes give a real
recovery signal independent of consumer behavior.

Cleared by `federation.gateway.recovered` with `kind="reachability"`
when a HALF_OPEN probe succeeds.

Payload: {
    "gateway_id": str,
    "consecutive_disconnects": int,
    "first_error_code": str,      # GATEWAY_DISCONNECTED | EDGE_UNREACHABLE
    "cooldown_s": float,
}
"""

# Gateway-wide health: recovery
FEDERATION_GATEWAY_RECOVERED = "federation.gateway.recovered"
"""
Emitted when a previously DEGRADED, UNHEALTHY, or liveness-stale gateway recovers.

Payload: {
    "gateway_id": str,
    "kind": str,    # "degradation" | "reachability" | "liveness"
    "reason": str,  # "first_success" | "probe_succeeded" | "heartbeat_resumed"
    "downtime_ms": int | omitted,  # present when kind="liveness"
}
"""

# Passive heartbeat staleness (traffic-independent; read-only w.r.t. routing)
FEDERATION_GATEWAY_LIVENESS_STALE = "federation.gateway.liveness.stale"
"""
Emitted when a non-cloud federated gateway exceeds the liveness alert threshold
(``ULG_FEDERATION_LIVENESS_ALERT_THRESHOLD_MS``, default 300s) without any
inbound signal. Distinct from ``federation.gateway.unhealthy``, which is
request-outcome-driven and excludes routing. This signal is observation-only:
the node is already routing-excluded via ``is_unreachable`` (>60s).

Cleared by ``federation.gateway.recovered`` with ``kind="liveness"`` when
heartbeat resumes (``not is_unreachable``).

Payload: {
    "gateway_id": str,
    "heartbeat_age_ms": int,
    "threshold_ms": int,
    "last_heartbeat_iso": str,
    "backend_type": str,
}
"""

# Relay-side edge container outage (UDS link failure)
FEDERATION_EDGE_CONTAINER_EXITED = "federation.edge.container.exited"

# Transport keepalive (websockets ping_interval / ping_timeout)
FEDERATION_LINK_TIMEOUT = "federation.link.timeout"

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
def FederationTelemetryReceived(
    remote_id: str,
    model_count: int,
    resource_summary: dict[str, Any],
    telemetry_age_ms: int | None = None,
    msg_type: str | None = None,
    catalog_model_count: int | None = None,
    loaded_model_count: int | None = None,
    count_source: str | None = None,
) -> Event:
    """Master received telemetry from Remote/Edge.

    Disambiguation fields (msg_type, catalog_model_count, loaded_model_count,
    count_source) clarify what model_count represents per message type.
    """
    payload = {
        "remote_id": remote_id,
        "model_count": model_count,
        "resource_summary": resource_summary,
        **(
            {"telemetry_age_ms": telemetry_age_ms}
            if telemetry_age_ms is not None
            else {}
        ),
        **({"msg_type": msg_type} if msg_type is not None else {}),
        **(
            {"catalog_model_count": catalog_model_count}
            if catalog_model_count is not None
            else {}
        ),
        **(
            {"loaded_model_count": loaded_model_count}
            if loaded_model_count is not None
            else {}
        ),
        **({"count_source": count_source} if count_source is not None else {}),
    }
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
    payload = {
        "request_id": request_id,
        "target_remote": target_remote,
        "model_id": model_id,
        **({"reason": reason} if reason is not None else {}),
    }
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


@event_factory
def FederationTelemetryWired(
    gateway_url: str,
    gateway_id: str,
) -> Event:
    """Confirm telemetry bridge wiring so master-side freshness waits can proceed."""
    return Event(
        signal=FEDERATION_TELEMETRY_WIRED,
        payload={"gateway_url": gateway_url, "gateway_id": gateway_id},
    )


@event_factory
def FederationRequestInferenceStartedForwarded(
    request_id: str | None,
    peer_count: int,
) -> Event:
    """Emit fanout confirmation for request-start signals forwarded to peers."""
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
    """Capture lifecycle telemetry application for model-state reconciliation traces."""
    return Event(
        signal=FEDERATION_MODEL_LIFECYCLE_EVENT,
        payload={
            "gateway_id": gateway_id,
            "msg_type": msg_type,
            "model_id": model_id,
        },
        scope="node",
    )


@event_factory
def FederationResourceUpdated(
    gateway_id: str,
    vram_free_mb: int,
    ram_free_mb: int,
) -> Event:
    """Capture resource refresh used by admission and routing feasibility logic."""
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
    """Record remote disconnect teardown after gateway removal from manager state."""
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
    """Track outbound VRAM probe dispatch to peer/device pairing for correlation."""
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
    """Record VRAM probe dispatch failure with explicit operational reason."""
    return Event(
        signal=FEDERATION_VRAM_REQUEST_FAILED,
        payload={"request_id": request_id, "reason": reason},
    )


@event_factory
def FederationVramResponseReceived(
    request_id: str,
    matched: bool,
) -> Event:
    """Capture VRAM probe response correlation success or orphaned-response mismatch."""
    return Event(
        signal=FEDERATION_VRAM_RESPONSE_RECEIVED,
        payload={"request_id": request_id, "matched": matched},
    )


@event_factory
def FederationActivationFilteredEmpty(
    gateway_id: str,
    available_count: int,
    activated_count: int,
) -> Event:
    """Gateway has available models but activation list is explicitly empty."""
    return Event(
        signal=FEDERATION_ACTIVATION_FILTERED_EMPTY,
        payload={
            "gateway_id": gateway_id,
            "available_count": available_count,
            "activated_count": activated_count,
        },
    )


@event_factory
def FederationCircuitBreakerRequestRejected(
    gateway_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Expose circuit-breaker admission guard outcomes for rejected model requests."""
    return Event(
        signal=FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED,
        payload={"gateway_id": gateway_id, "model_id": model_id, "reason": reason},
    )


@event_factory
def FederationGatewayDegraded(
    gateway_id: str,
    consecutive_timeouts: int,
    first_error_code: str,
) -> Event:
    """Federated gateway crossed timeout threshold; coordination only, ¬excluded."""
    return Event(
        signal=FEDERATION_GATEWAY_DEGRADED,
        role="coordination",
        payload={
            "gateway_id": gateway_id,
            "consecutive_timeouts": consecutive_timeouts,
            "first_error_code": first_error_code,
        },
    )


@event_factory
def FederationGatewayUnhealthy(
    gateway_id: str,
    consecutive_disconnects: int,
    first_error_code: str,
    cooldown_s: float,
) -> Event:
    """Federated gateway unreachable; excluded from routing for cooldown_s."""
    return Event(
        signal=FEDERATION_GATEWAY_UNHEALTHY,
        role="coordination",
        payload={
            "gateway_id": gateway_id,
            "consecutive_disconnects": consecutive_disconnects,
            "first_error_code": first_error_code,
            "cooldown_s": cooldown_s,
        },
    )


@event_factory
def FederationGatewayRecovered(
    gateway_id: str,
    kind: str,
    reason: str,
    downtime_ms: int | None = None,
) -> Event:
    """Previously DEGRADED, UNHEALTHY, or liveness-stale gateway recovered."""
    payload: dict[str, Any] = {
        "gateway_id": gateway_id,
        "kind": kind,
        "reason": reason,
    }
    if downtime_ms is not None:
        payload["downtime_ms"] = downtime_ms
    return Event(
        signal=FEDERATION_GATEWAY_RECOVERED,
        role="coordination" if kind != "liveness" else "observation",
        payload=payload,
    )


@event_factory
def FederationGatewayLivenessStale(
    gateway_id: str,
    heartbeat_age_ms: int,
    threshold_ms: int,
    last_heartbeat_iso: str,
    backend_type: str,
) -> Event:
    """Passive heartbeat staleness alert — no routing side effects."""
    return Event(
        signal=FEDERATION_GATEWAY_LIVENESS_STALE,
        role="observation",
        payload={
            "gateway_id": gateway_id,
            "heartbeat_age_ms": heartbeat_age_ms,
            "threshold_ms": threshold_ms,
            "last_heartbeat_iso": last_heartbeat_iso,
            "backend_type": backend_type,
        },
    )


@event_factory
def FederationEdgeContainerExited(
    node_id: str,
    relay_stargate_id: str,
    error_type: str,
    socket_path: str,
) -> Event:
    """Relay detected local edge container UDS link failure."""
    return Event(
        signal=FEDERATION_EDGE_CONTAINER_EXITED,
        role="observation",
        scope="node",
        payload={
            "node_id": node_id,
            "relay_stargate_id": relay_stargate_id,
            "error_type": error_type,
            "socket_path": socket_path,
        },
    )


@event_factory
def FederationLinkTimeout(
    *,
    link_role: str,
    peer_id: str,
    close_code: int | None,
    close_reason: str,
    cause: str,
) -> Event:
    """Native WS keepalive ping missed pong (websockets 1011 keepalive ping timeout)."""
    return Event(
        signal=FEDERATION_LINK_TIMEOUT,
        role="observation",
        scope="node",
        payload={
            "link_role": link_role,
            "peer_id": peer_id,
            "close_code": close_code,
            "close_reason": close_reason,
            "cause": cause,
        },
    )
