"""Auto-derived map step concurrency from federated model capacity.

Computes a safe ``max_concurrency`` ceiling for a map step by summing the
``parallel_slots`` reported by every gateway that currently has the assigned
model loaded or busy. The federation and routing imports are kept lazy so the
executor module remains importable in test environments where those subsystems
are not wired up; on any exception during capacity discovery the function
returns ``None`` and the caller falls back to uncapped dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .map_executor import MapExecutor


def _gateway_in_active_set(gw: object, active_nodes: set[str]) -> bool:
    """True when this gateway's catalog id is in loaded_on | busy_on.

    Federated catalogs key active sets by ``remote_stargate_id``; the local
    gateway uses ``local_stargate_id`` (node_id / gateway_id when remote is empty).
    """
    remote_id = getattr(gw, "remote_stargate_id", "") or ""
    if remote_id:
        return remote_id in active_nodes
    node_id = getattr(gw, "node_id", "") or ""
    gateway_id = getattr(gw, "gateway_id", "") or ""
    return node_id in active_nodes or gateway_id in active_nodes


def derive_model_capacity(
    executor: MapExecutor, pool_assignments: dict[int, str]
) -> int | None:
    """Sum parallel_slots across loaded/busy gateways for the assigned model.

    Uses the first pool assignment as the representative model_id (all
    iterations in a batch pipeline target the same model). Returns None
    when the proxy, federated_manager, or model_resources are unavailable
    so the caller falls back to uncapped dispatch.

    Only nodes where the model is currently loaded or busy contribute —
    nodes in loading/draining/unhealthy state are excluded, matching the
    filter used by GET /api/v1/model-capacity/{model_id}.
    """
    model_id = next(iter(pool_assignments.values()), None)
    if not model_id:
        return None
    proxy = getattr(executor._runtime, "_proxy", None)
    if proxy is None:
        return None
    fm = getattr(proxy, "federated_manager", None)
    if fm is None:
        return None
    gm = getattr(proxy, "gateway_manager", None)
    try:
        from systems.federation import get_federation_integration
        from systems.routing.selection.catalog import get_model_status_map

        fed = get_federation_integration()
        local_id = fed.config.stargate_id if fed and fed.config else "local"
        status_map = get_model_status_map(local_id, gm, fm)
        summary = status_map.get(model_id, {})
        active_nodes: set[str] = set(summary.get("loaded_on", [])) | set(
            summary.get("busy_on", [])
        )
        gateways = fm.get_all_gateways()
    except Exception:
        return None
    total = 0
    found = False
    for gw in gateways:
        meta = gw.model_resources.get(model_id)
        if meta is None:
            continue
        if not _gateway_in_active_set(gw, active_nodes):
            continue
        found = True
        total += meta.get("parallel_slots", 1)
    return total if found else None
