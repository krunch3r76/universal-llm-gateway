"""
Collects Stargate snapshots from federated gateways.

Bridges the selection system to the unified Stargate abstraction.

Post-unification: All Gateway access via federation (no local Gateway).
"""

from __future__ import annotations

import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .collector import is_gateway_dispatchable
from .types import Stargate  # Stargate uses ModelId in its type annotations

if TYPE_CHECKING:
    from .types import Gateway

logger = get_logger(__name__)


def _map_model_resources_to_details(
    model_resources: dict[Any, dict[str, Any]],
) -> dict[Any, dict[str, int]]:
    """Normalize model_resources payload into routing model_details shape."""
    return {
        model_id: {
            "vram_usage": int(res.get("vram_usage", 0)),
            "ram_usage": int(res.get("ram_usage", 0)),
            "max_concurrent_requests": int(res.get("max_concurrent_requests", 1)),
        }
        for model_id, res in model_resources.items()
    }


def stargate_to_gateway(stargate: Stargate) -> Gateway:
    """
    Convert Stargate snapshot to Gateway snapshot for DecisionEngine.

    Post-unification: All Stargates are federated (no local Gateway).

    Identifier Semantics:
        - Gateway.name = stargate.ref.gateway_id (federated ID)
        - stargate_id is NOT used as Gateway.name (different concepts)

    Invariant:
        ∀ stargate: gateway.ref is FederatedGateway
        ∧ gateway.name = stargate.ref.gateway_id

    Args:
        stargate: Stargate snapshot (federated)

    Returns:
        Gateway snapshot compatible with DecisionEngine.select()
    """
    from .types import Gateway as GatewayType

    # All gateways are federated - use gateway_id
    name = stargate.ref.gateway_id

    gateway_obj = GatewayType(
        ref=stargate.ref,
        name=name,
        ram_free_mb=stargate.ram_free_mb,
        vram_free_mb=stargate.vram_free_mb,
        ram_total_mb=stargate.ram_total_mb,
        vram_total_mb=stargate.vram_total_mb,
        loaded_models=stargate.loaded_models,
        busy_models=stargate.busy_models,
        loading_models=stargate.loading_models,
        available_models=stargate.available_models,
        model_details=stargate.model_details,
        active_requests=stargate.active_requests,
        telemetry_timestamp=stargate.telemetry_timestamp,
        last_heartbeat=stargate.last_heartbeat,
        remote_stargate_id=stargate.ref.remote_stargate_id,
        node_id=getattr(stargate.ref, "node_id", ""),
        is_cloud=getattr(stargate.ref, "is_cloud", False),
    )
    return gateway_obj


def federated_gateways_to_routing_candidates(
    federated_gateways: list,
) -> list[Gateway]:
    """
    Convert FederatedGateway instances directly to Gateway snapshots.

    For router-only Master mode where FederatedGatewayManager provides
    gateways directly without the intermediate Stargate abstraction.

    Invariant: ∀ fg: FederatedGateway, gateway.name = fg.gateway_id

    Args:
        federated_gateways: List of FederatedGateway from FederatedGatewayManager

    Returns:
        List of Gateway snapshots for DecisionEngine.select()
    """
    from .types import Gateway as GatewayType

    gateways = []
    for fg in federated_gateways:
        if not is_gateway_dispatchable(fg):
            continue
        model_details = _map_model_resources_to_details(fg.model_resources)

        gateways.append(
            GatewayType(
                name=fg.gateway_id,
                ref=fg,
                vram_total_mb=fg.vram_total_mb,
                vram_free_mb=fg.vram_free_mb,
                ram_total_mb=fg.ram_total_mb,
                ram_free_mb=fg.ram_free_mb,
                loaded_models=fg.loaded_models,
                busy_models=fg.busy_models,
                loading_models=fg.loading_models,
                available_models=fg.available_models,
                active_requests=fg.active_requests,
                telemetry_timestamp=fg.telemetry_timestamp,
                last_heartbeat=fg.last_heartbeat,
                model_details=model_details,
                model_loaded_at=getattr(fg, "model_loaded_at", {}),
                remote_stargate_id=fg.remote_stargate_id,
                node_id=getattr(fg, "node_id", ""),
                is_cloud=fg.is_cloud,
            )
        )

    return gateways


class StargateCollisionError(Exception):
    """Raised when federation collection sees duplicate stargate_id values."""

    pass


def validate_stargate_pool(stargates: list[Stargate]) -> None:
    """
    Fail-fast if duplicate stargate_ids detected.

    Raises:
        StargateCollisionError: If duplicates found
    """
    counts = Counter(sg.stargate_id for sg in stargates)
    duplicates = {stargate_id for stargate_id, count in counts.items() if count > 1}
    if duplicates:
        raise StargateCollisionError(
            f"Duplicate stargate_id detected: {duplicates}. "
            "Each Stargate must have a unique ID."
        )


def _collect_federated_stargates(
    federated_manager: Any | None,
    snapshot_time: float,
) -> list[Stargate]:
    """
    Collect federated gateways as Stargate snapshots.

    Returns:
        List of Stargate snapshots from federation
    """
    if not federated_manager:
        return []

    healthy_gateways = federated_manager.get_healthy_gateways()
    stargates: list[Stargate] = []
    for fed_gw in healthy_gateways:
        logger.debug(
            f"Collecting federated {fed_gw.gateway_id}: "
            f"catalog={len(fed_gw.available_models)}, "
            f"loaded={len(fed_gw.loaded_models)}"
        )
        model_details = _map_model_resources_to_details(fed_gw.model_resources)
        logger.info(
            f"📊 [PHASE1] Stargate {fed_gw.gateway_id}: "
            f"model_resources={len(fed_gw.model_resources)}, "
            f"model_details={len(model_details)}, "
            f"sample: {list(model_details.items())[:2] if model_details else 'empty'}"
        )
        stargates.append(
            Stargate(
                stargate_id=fed_gw.remote_stargate_id,
                ref=fed_gw,
                ram_free_mb=fed_gw.ram_free_mb,
                vram_free_mb=fed_gw.vram_free_mb,
                ram_total_mb=getattr(fed_gw, "ram_total_mb", 0),
                vram_total_mb=getattr(fed_gw, "vram_total_mb", 0),
                loaded_models=fed_gw.loaded_models,
                busy_models=fed_gw.busy_models,
                loading_models=getattr(fed_gw, "loading_models", frozenset()),
                available_models=fed_gw.available_models,
                active_requests=fed_gw.active_requests,
                # FIXED: Use actual remote timestamp, not snapshot time
                telemetry_timestamp=(
                    getattr(fed_gw, "telemetry_timestamp", 0.0) or snapshot_time
                ),
                last_heartbeat=getattr(fed_gw, "last_update_time", 0.0) or 0.0,
                model_details=model_details,
            )
        )
    return stargates


def collect_stargates(
    _local_stargate_id: str,
    federated_manager: Any | None,
) -> list[Stargate]:
    """
    Collect all reachable Stargates from federation.

    Post-unification: All Stargates accessed via federation (no local Gateway).

    Invariant: ∀ stargate ∈ result: stargate accessed via federation

    Args:
        _local_stargate_id: Preserved for signature compatibility
            (unused post-unification)
        federated_manager: FederatedGatewayManager (or None if not in federation)

    Returns:
        List of Stargate snapshots from federation

    Raises:
        StargateCollisionError: If duplicate stargate_ids detected
    """
    stargates = _collect_federated_stargates(federated_manager, time.time())

    # Fail-fast on collision
    validate_stargate_pool(stargates)

    logger.debug(f"collect_stargates: {len(stargates)} federated stargate(s) collected")

    return stargates
