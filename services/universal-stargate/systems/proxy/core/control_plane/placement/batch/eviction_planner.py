"""
Batch-level eviction planning.

Computes eviction plan that frees enough resources for entire batch,
not just individual requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import SingleGatewayManager
    from systems.routing.selection.decision.types import Gateway

    from ..single.operations import ModelRoutingOperations
    from .request import InferenceRequest

logger = get_logger(__name__)


@dataclass
class BatchEvictionPlan:
    """Eviction plan for making room for a batch."""

    models_to_evict: list[tuple[str, str]]  # (model_id, gateway_name)
    freed_vram_mb: int
    freed_ram_mb: int


async def compute_batch_eviction_plan(
    cold_requests: list[InferenceRequest],
    gateways: list[Gateway],
    gateway_manager: SingleGatewayManager,
) -> BatchEvictionPlan | None:
    """
    Compute eviction plan to make room for cold requests.

    Returns None if eviction cannot free enough resources.
    """
    # Calculate how much we need to free
    vram_deficit, ram_deficit = _calculate_resource_deficit(cold_requests, gateways)

    if vram_deficit == 0 and ram_deficit == 0:
        return None  # No eviction needed

    # Identify models that cannot be evicted
    protected_keys = _identify_protected_routing_keys(cold_requests)

    # Find all evictable models across gateways
    evictable_models = _discover_evictable_models(gateways, protected_keys)

    if not evictable_models:
        logger.debug("No evictable models found")
        return None

    # Select models to evict using greedy algorithm
    selected, freed_vram, freed_ram = _select_models_to_evict(
        evictable_models, vram_deficit, ram_deficit
    )

    if freed_vram < vram_deficit or freed_ram < ram_deficit:
        logger.warning(
            f"Eviction insufficient: can free {freed_vram}MB VRAM, "
            f"{freed_ram}MB RAM; need {vram_deficit}MB, {ram_deficit}MB"
        )
        # Return what we can - caller will defer some requests

    logger.info(
        f"Eviction plan: evict {len(selected)} models, "
        f"free {freed_vram}MB VRAM, {freed_ram}MB RAM"
    )

    return BatchEvictionPlan(
        models_to_evict=selected,
        freed_vram_mb=freed_vram,
        freed_ram_mb=freed_ram,
    )


def _calculate_resource_deficit(
    cold_requests: list[InferenceRequest],
    gateways: list[Gateway],
) -> tuple[int, int]:
    """
    Calculate VRAM and RAM deficit for cold requests.

    Returns:
        (vram_deficit, ram_deficit) in MB
    """
    available_vram = sum(g.vram_free_mb for g in gateways)
    available_ram = sum(g.ram_free_mb for g in gateways)

    needed_vram = sum(r.vram_required_mb for r in cold_requests if r.is_gpu)
    needed_ram = sum(r.ram_required_mb for r in cold_requests if not r.is_gpu)

    vram_deficit = max(0, needed_vram - available_vram)
    ram_deficit = max(0, needed_ram - available_ram)

    return vram_deficit, ram_deficit


def _identify_protected_routing_keys(
    cold_requests: list[InferenceRequest],
) -> set[str]:
    """
    Identify routing keys that cannot be evicted.

    Protected = in-flight, reserved, or target models for this batch.

    Returns:
        Set of protected routing keys
    """
    from src.core.gateway_tracker import gateway_tracker

    # Get protected models (in-flight or reserved)
    routing_keys_in_flight = gateway_tracker.get_routing_keys_in_use_globally()

    # TODO: Inject coordinator as dependency instead of global access
    coordinator = None
    routing_keys_reserved = set()
    if coordinator:
        routing_keys_reserved = coordinator.get_routing_keys_with_reservations()

    # Also protect models we're trying to load
    target_routing_keys = {r.model_id.routing_key for r in cold_requests}

    return routing_keys_in_flight | routing_keys_reserved | target_routing_keys


def _discover_evictable_models(
    gateways: list[Gateway],
    protected_keys: set[str],
) -> list[tuple[str, str, int, int]]:
    """
    Find all evictable models across gateways.

    Returns:
        List of (model_id, gateway_name, vram_mb, ram_mb) tuples
    """
    evictable_models: list[tuple[str, str, int, int]] = []

    for gateway in gateways:
        idle_models = [
            mid
            for mid in gateway.loaded_models
            if mid not in gateway.busy_models and mid not in gateway.loading_models
        ]

        for model_id in idle_models:
            routing_key = model_id.routing_key

            if routing_key in protected_keys:
                continue

            # Get resource usage from model_details (populated by collect_gateways)
            model_info = gateway.model_details.get(model_id, {})
            vram = model_info.get("vram_usage", 0)
            ram = model_info.get("ram_usage", 0)
            evictable_models.append((model_id, gateway.name, vram, ram))

    return evictable_models


def _select_models_to_evict(
    evictable_models: list[tuple[str, str, int, int]],
    vram_deficit: int,
    ram_deficit: int,
) -> tuple[list[tuple[str, str]], int, int]:
    """
    Select models to evict using greedy largest-first algorithm.

    Args:
        evictable_models: List of (model_id, gateway_name, vram_mb, ram_mb)
        vram_deficit: VRAM needed in MB
        ram_deficit: RAM needed in MB

    Returns:
        (selected_models, freed_vram_mb, freed_ram_mb)
        where selected_models is list of (model_id, gateway_name)
    """
    # Greedy selection: largest models first until deficit covered
    sorted_models = sorted(evictable_models, key=lambda x: x[2] + x[3], reverse=True)

    selected: list[tuple[str, str]] = []
    freed_vram = 0
    freed_ram = 0

    for model_id, gateway_name, vram, ram in sorted_models:
        if freed_vram >= vram_deficit and freed_ram >= ram_deficit:
            break
        selected.append((model_id, gateway_name))
        freed_vram += vram
        freed_ram += ram

    return selected, freed_vram, freed_ram


async def execute_eviction_plan(
    plan: BatchEvictionPlan,
    gateway_manager: SingleGatewayManager,
    routing_ops: ModelRoutingOperations,
) -> tuple[int, int]:
    """
    Execute eviction plan with cache synchronization.

    Returns:
        (freed_vram_mb, freed_ram_mb)
    """
    from .cache_sync import sync_cache_after_eviction

    # Group by gateway for efficiency
    by_gateway: dict[str, list[str]] = {}
    for model_id, gateway_name in plan.models_to_evict:
        by_gateway.setdefault(gateway_name, []).append(model_id)

    gateway = gateway_manager.get_gateway()
    if not gateway:
        logger.warning("No gateway available for eviction")
        return 0, 0

    healthy_gateways = {gateway.config.name: gateway}

    # Execute evictions
    evicted: list[tuple[str, str]] = []

    for gateway_name, models in by_gateway.items():
        gw = healthy_gateways.get(gateway_name)
        if not gw:
            logger.warning(f"Gateway {gateway_name} not found for eviction")
            continue

        for model_id in models:
            try:
                logger.info(f"🗑️ Evicting {model_id} from {gateway_name}")
                await routing_ops._loading_ops.unload_model(gw, model_id)
                evicted.append((model_id, gateway_name))
            except Exception as e:
                logger.warning(f"Failed to evict {model_id}: {e}")

    # NEW: Synchronize cache after eviction
    # TODO: Inject coordinator as dependency instead of global access
    coordinator = None
    if coordinator and evicted:
        await sync_cache_after_eviction(
            evicted, coordinator, healthy_gateways, timeout_per_model=2.0
        )

    return plan.freed_vram_mb, plan.freed_ram_mb
