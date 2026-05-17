"""
Topology snapshot builder for MODEL_NOT_FOUND rejection diagnostics.

Extracted from the former monolithic rejection.py during modularization.
"""

from typing import TYPE_CHECKING, Any

from model_id import ModelId

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )


def _build_topology_snapshot(
    federated_manager: "FederatedGatewayManager",
    model_id: ModelId,
) -> dict[str, Any]:
    """Build topology-explaining snapshot for MODEL_NOT_FOUND rejection.

    Edge gateways (federated remote stargates) are reported separately from
    cloud gateways (``backend_type == "cloud_api"``) because operators reason
    about "edges I configured" — folding cloud providers into the edge
    denominator yields a misleading hint (``connected_edges=7/7`` when only
    2 federated edges exist and both are up).

    Edge classes:
      - connected_edges: federated gateways currently reachable
      - unreachable_edges: federated gateways with stale heartbeat, flagged
        with whether their cached catalog held the requested model (the
        "target edge is down" diagnostic case)
      - cached_only_edges: federated gateways no longer registered but whose
        catalog is retained from the most recent disconnect
      - not_seen_remotes: remote stargates declared in federation config
        (``FederationConfig.remotes`` → ``_remote_configs``) that have never
        produced a registered gateway since startup (the "configured-but-down"
        case — solves the "1/2 vs 1/1" denominator surprise)

    Cloud surface lives under ``connected_cloud_gateways`` so a missing-model
    failure still exposes the full provider set without polluting the edge
    denominator used in the HTTP hint.
    """
    registered = list(federated_manager.get_all_gateways())
    cache: dict[str, Any] = getattr(federated_manager, "_catalog_cache", {})
    remote_configs: dict[str, Any] = getattr(federated_manager, "_remote_configs", {})

    connected_edges: list[str] = []
    connected_cloud_gateways: list[str] = []
    unreachable_edges: list[dict[str, Any]] = []
    for gw in registered:
        if gw.is_cloud:
            connected_cloud_gateways.append(gw.gateway_id)
            continue
        if gw.is_unreachable:
            unreachable_edges.append(
                {
                    "gateway_id": gw.gateway_id,
                    "remote_id": gw.remote_stargate_id,
                    "last_heartbeat_age_ms": gw.heartbeat_age_ms,
                    "cached_catalog_match": model_id in gw.available_models,
                }
            )
        else:
            connected_edges.append(gw.gateway_id)

    registered_ids = {gw.gateway_id for gw in registered}
    cached_only_edges: list[dict[str, Any]] = []
    for gw_id, entry in cache.items():
        if gw_id in registered_ids:
            continue
        cached_only_edges.append(
            {
                "gateway_id": gw_id,
                "cached_catalog_match": model_id in entry["available_models"],
            }
        )

    seen_remote_ids = {gw.remote_stargate_id for gw in registered if not gw.is_cloud}
    not_seen_remotes: list[str] = sorted(
        remote_id for remote_id in remote_configs if remote_id not in seen_remote_ids
    )

    configured_remote_count = len(remote_configs)
    edges_present = (
        len(connected_edges) + len(unreachable_edges) + len(cached_only_edges)
    )
    total_edges_known = max(
        edges_present + len(not_seen_remotes),
        configured_remote_count,
    )

    return {
        "connected_edge_count": len(connected_edges),
        "connected_edges": connected_edges,
        "unreachable_edge_count": len(unreachable_edges),
        "unreachable_edges": unreachable_edges,
        "cached_only_edge_count": len(cached_only_edges),
        "cached_only_edges": cached_only_edges,
        "configured_remote_count": configured_remote_count,
        "not_seen_remote_count": len(not_seen_remotes),
        "not_seen_remotes": not_seen_remotes,
        "total_edges_known": total_edges_known,
        "connected_cloud_gateway_count": len(connected_cloud_gateways),
        "connected_cloud_gateways": connected_cloud_gateways,
    }
