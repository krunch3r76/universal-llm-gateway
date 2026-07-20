"""Federation gateway health and link/container outage event signals.

Degraded/unhealthy/recovered/liveness-stale plus edge-container exit and
link timeout. Imported via the ``federation_signaling`` package facade."""

# ruff: noqa: N802

from typing import Any

from universal_event_bus import Event, event_factory

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

FEDERATION_EDGE_CONTAINER_EXITED = "federation.edge.container.exited"

FEDERATION_LINK_TIMEOUT = "federation.link.timeout"


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
