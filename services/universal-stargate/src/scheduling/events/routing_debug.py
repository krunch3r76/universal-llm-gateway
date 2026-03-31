# ruff: noqa: N802
"""Debug observability signals for routing decisions and gateway state changes.

Emitted on anomalies only — not on every routing call. Designed for
diagnosing intermittent gateway dropouts, health filter issues, and
disconnect/reconnect races.

Signals:
    routing.debug.gateway.dropout — health filter dropped a gateway from routing
    routing.debug.gateway.removed — gateway removed on edge/remote disconnect
    routing.debug.gateway.registered — new gateway registered (initial or reconnect)
"""

from typing import Any

from universal_event_bus import Event, event_factory

ROUTING_DEBUG_GATEWAY_DROPOUT = "routing.debug.gateway.dropout"
"""
One or more gateways dropped by the health filter (is_unreachable=True).

Only emitted when at least one gateway fails the health check — not on
every routing call. This is the primary signal for the intermittent
localhost-gateway dropout investigation.

Payload: {
    "model_id": str,
    "stage": str,                 # "health_filter"
    "all_gateway_ids": list[str],
    "surviving_gateway_ids": list[str],
    "dropped_gateway_ids": list[str],
    "detail": dict[str, dict]     # per-dropped-gateway health metrics
}
"""

ROUTING_DEBUG_GATEWAY_REMOVED = "routing.debug.gateway.removed"
"""
Gateways removed due to edge/remote disconnect.

Emitted from remove_remote_gateways() — fires on every disconnect event.
Captures pre/post gateway state for race condition analysis.

Payload: {
    "remote_stargate_id": str,
    "removed_gateway_ids": list[str],
    "remaining_gateway_ids": list[str],
}
"""

ROUTING_DEBUG_GATEWAY_REGISTERED = "routing.debug.gateway.registered"
"""
New gateway registered in the federated gateway manager.

Emitted from _ensure_gateway() when a gateway ID is seen for the first time.
Tracks reconnect races where catalog may be empty.

Payload: {
    "gateway_id": str,
    "remote_stargate_id": str,
    "node_id": str | None,
    "catalog_size": int,
    "is_http_polling": bool,
}
"""


@event_factory
def RoutingDebugGatewayDropout(
    model_id: str,
    stage: str,
    all_gateway_ids: list[str],
    surviving_gateway_ids: list[str],
    dropped_gateway_ids: list[str],
    detail: dict[str, Any],
) -> Event:
    """Emitted when one or more gateways are dropped by the health filter.

    Helps diagnose intermittent gateway dropouts. Payload includes all,
    surviving, and dropped gateway IDs and health metrics for dropped gateways.
    """
    return Event(
        signal=ROUTING_DEBUG_GATEWAY_DROPOUT,
        payload={
            "model_id": model_id,
            "stage": stage,
            "all_gateway_ids": all_gateway_ids,
            "surviving_gateway_ids": surviving_gateway_ids,
            "dropped_gateway_ids": dropped_gateway_ids,
            "detail": detail,
        },
    )


@event_factory
def RoutingDebugGatewayRemoved(
    remote_stargate_id: str,
    removed_gateway_ids: list[str],
    remaining_gateway_ids: list[str],
) -> Event:
    """Emitted when gateways are removed on edge/remote disconnect.

    Captures pre/post gateway state for race condition analysis.
    """
    return Event(
        signal=ROUTING_DEBUG_GATEWAY_REMOVED,
        payload={
            "remote_stargate_id": remote_stargate_id,
            "removed_gateway_ids": removed_gateway_ids,
            "remaining_gateway_ids": remaining_gateway_ids,
        },
    )


@event_factory
def RoutingDebugGatewayRegistered(
    gateway_id: str,
    remote_stargate_id: str,
    node_id: str | None,
    catalog_size: int,
    is_http_polling: bool,
) -> Event:
    """Emitted when a new gateway is registered (initial or reconnect).

    Tracks reconnect races where the catalog might be empty. Payload includes
    gateway details, catalog size, and HTTP polling status.
    """
    return Event(
        signal=ROUTING_DEBUG_GATEWAY_REGISTERED,
        payload={
            "gateway_id": gateway_id,
            "remote_stargate_id": remote_stargate_id,
            "node_id": node_id,
            "catalog_size": catalog_size,
            "is_http_polling": is_http_polling,
        },
    )
