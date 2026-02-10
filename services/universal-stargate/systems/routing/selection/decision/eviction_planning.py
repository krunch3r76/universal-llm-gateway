"""
Eviction planning for feasibility evaluation.

Computes which models to evict to make room for a new model.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .resource_checks import _compute_loading_reservation
from .types import EvictionPlanSummary

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)


def _compute_eviction_plan(
    gateway: "Gateway",
    placement: "Placement",
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    config: dict | None = None,
    routing_key_tracker: "RoutingKeyTracker | None" = None,
) -> EvictionPlanSummary | None:
    """
    Compute eviction plan to make room for model.

    Args:
        gateway: Gateway to compute eviction plan for
        placement: Model placement requirements
        requirements_lookup: MANDATORY function to look up (vram_mb, ram_mb)
                           for loading models (in-memory, no I/O)
        config: Optional config dict for resource margins
        routing_key_tracker: Tracker for in-flight routing keys (eviction protection).
            REQUIRED for Master mode - prevents eviction of models with active requests.

    Returns None if eviction cannot provide enough resources.
    """
    # Log entry point with full context
    logger.info(
        f"🔍 EVICTION EVAL for {placement.model_id} on {gateway.name}: "
        f"Need {placement.vram_mb}MB VRAM, {placement.ram_mb}MB RAM | "
        f"Currently available: {gateway.vram_free_mb}MB VRAM, "
        f"{gateway.ram_free_mb}MB RAM | "
        f"Loaded models: {list(gateway.loaded_models)}"
    )

    # Subtract loading model reservations from available
    vram_reserved = 0
    ram_reserved = 0

    if gateway.loading_models:
        try:
            vram_reserved, ram_reserved, _ = _compute_loading_reservation(
                gateway, placement.model_id, requirements_lookup
            )
        except ValueError as e:
            # Missing requirements for loading model - cannot plan eviction
            logger.error(f"Eviction planning blocked: {e}")
            return None

    # Adjust effective free before eviction calculations
    # Note: Can be negative if loading models exceed current hardware free
    effective_vram_free = gateway.vram_free_mb - vram_reserved
    effective_ram_free = gateway.ram_free_mb - ram_reserved

    # Log if overcommitted (negative effective free)
    if effective_vram_free < 0:
        logger.debug(
            f"Gateway {gateway.name} VRAM overcommitted during eviction planning: "
            f"effective={effective_vram_free}MB (hardware={gateway.vram_free_mb}MB, "
            f"reserved={vram_reserved}MB)"
        )

    # Get idle models (loaded but not busy)
    idle_models = [
        mid
        for mid in gateway.loaded_models
        if mid not in gateway.busy_models and mid not in gateway.loading_models
    ]

    logger.debug(
        f"Eviction planning for {placement.model_id}: "
        f"loaded={list(gateway.loaded_models)}, "
        f"busy={list(gateway.busy_models)}, "
        f"idle={idle_models}"
    )

    if not idle_models:
        logger.debug("No idle models available for eviction")
        return None

    # Filter out models with in-flight requests (eviction protection)
    idle_before_inflight_filter = len(idle_models)
    if routing_key_tracker is not None:
        routing_keys_in_flight = (
            routing_key_tracker.get_routing_keys_in_flight_globally()
        )
        idle_models = [
            mid
            for mid in idle_models
            if str(mid.routing_key) not in routing_keys_in_flight
        ]
        logger.debug(
            f"After filtering in-flight: {idle_models}, "
            f"in_flight_keys={routing_keys_in_flight}"
        )
    else:
        routing_keys_in_flight = set()
        logger.debug("No routing_key_tracker provided - skipping in-flight filter")

    inflight_filtered = idle_before_inflight_filter - len(idle_models)

    # Filter out variants of target model
    target_model_id = placement.model_id
    evictable = [mid for mid in idle_models if mid != target_model_id]

    logger.debug(
        f"After filtering target variants: evictable={evictable}, "
        f"target_key={target_model_id}"
    )

    # Log eviction candidates with their estimated VRAM
    evictable_with_vram = []
    for mid in evictable:
        vram_usage, _ = gateway.get_model_resource_usage(mid)
        evictable_with_vram.append(f"{mid}({vram_usage}MB)")
    logger.info(
        f"🔍 EVICTION CANDIDATES: {len(evictable)} evictable models "
        f"(after filtering {len(list(gateway.busy_models))} busy, "
        f"{inflight_filtered} in-flight, "
        f"{len(idle_models) - len(evictable)} same routing key): "
        f"{evictable_with_vram}"
    )

    if not evictable:
        logger.debug("No evictable models after filtering")
        return None

    # Calculate max freeable resources
    freed_vram_catalog = 0
    freed_ram_catalog = 0
    models_to_evict = []

    for model_id in evictable:
        vram_usage, ram_usage = gateway.get_model_resource_usage(model_id)
        logger.debug(
            f"Model {model_id}: vram={vram_usage}MB, ram={ram_usage}MB, "
            f"details={gateway.model_details.get(model_id, {})}"
        )
        models_to_evict.append(model_id)
        freed_vram_catalog += vram_usage
        freed_ram_catalog += ram_usage

    # Check if eviction provides enough
    ram_margin = 1.03  # 3% safety margin
    vram_margin_config = (
        config.get("resource_margins", {}).get("vram_margin") if config else None
    )
    vram_margin = vram_margin_config if vram_margin_config is not None else 1.0
    ram_needed = int(placement.ram_mb * ram_margin)
    vram_needed = int(placement.vram_mb * vram_margin)

    # Conservative estimate combining actual measurements + catalog + totals
    # Invariant: available = max(catalog_estimate, hardware_measured,
    #                            total_if_full_eviction)

    # Method 1: Catalog-based (what we plan to evict) + effective free
    # (after loading reservation)
    catalog_based_vram = effective_vram_free + freed_vram_catalog
    catalog_based_ram = effective_ram_free + freed_ram_catalog

    # Method 2: Hardware-measured actual usage (what's really used)
    hardware_used_vram = gateway.vram_total_mb - gateway.vram_free_mb
    hardware_used_ram = gateway.ram_total_mb - gateway.ram_free_mb

    # For full eviction: use actual hardware measurements
    # For partial eviction: use catalog estimates
    if len(models_to_evict) == len(gateway.loaded_models) and len(models_to_evict) > 0:
        # Full eviction - freed amount = actual hardware-measured usage
        hardware_freed_vram = hardware_used_vram
        hardware_freed_ram = hardware_used_ram
        hardware_based_vram = effective_vram_free + hardware_freed_vram
        hardware_based_ram = effective_ram_free + hardware_freed_ram

        # Conservative: max of catalog vs hardware vs total
        # (total is fallback if both underestimate)
        total_vram = max(catalog_based_vram, hardware_based_vram, gateway.vram_total_mb)
        total_ram = max(catalog_based_ram, hardware_based_ram, gateway.ram_total_mb)

        logger.debug(
            f"Full eviction ({len(models_to_evict)} models): "
            f"catalog={catalog_based_vram}MB, hardware={hardware_based_vram}MB, "
            f"total={gateway.vram_total_mb}MB, using max={total_vram}MB VRAM"
        )
    else:
        # Partial eviction - use catalog estimates (no per-model hardware metrics)
        total_vram = catalog_based_vram
        total_ram = catalog_based_ram
        logger.debug(
            f"Partial eviction "
            f"({len(models_to_evict)}/{len(gateway.loaded_models)} models): "
            f"catalog_based={catalog_based_vram}MB VRAM"
        )

    logger.debug(
        f"Eviction calculation: "
        f"catalog_freed={freed_vram_catalog}MB VRAM, "
        f"total_available={total_vram}MB VRAM (need {vram_needed}MB), "
        f"total_available={total_ram}MB RAM (need {ram_needed}MB)"
    )

    # Check BOTH resources for hybrid models (vram_mb > 0 AND ram_mb > 0)
    if placement.vram_mb > 0 and total_vram < vram_needed:
        logger.warning(
            f"❌ EVICTION FAILED for {placement.model_id}: "
            f"Insufficient VRAM even with eviction - need {vram_needed}MB, "
            f"can only get {total_vram}MB (current free: {gateway.vram_free_mb}MB + "
            f"freeable: {freed_vram_catalog}MB from {len(models_to_evict)} models)"
        )
        return None
    if placement.ram_mb > 0 and total_ram < ram_needed:
        logger.warning(
            f"❌ EVICTION FAILED for {placement.model_id}: "
            f"Insufficient RAM even with eviction - need {ram_needed}MB, "
            f"can only get {total_ram}MB (current free: {gateway.ram_free_mb}MB + "
            f"freeable: {freed_ram_catalog}MB from {len(models_to_evict)} models)"
        )
        return None

    logger.info(
        f"✅ EVICTION PLAN for {placement.model_id}: "
        f"evict {[str(m) for m in models_to_evict]} → "
        f"free {freed_vram_catalog}MB VRAM, {freed_ram_catalog}MB RAM"
    )

    # Calculate eviction cost
    eviction_count = len(models_to_evict)
    # Placeholder cost - will be refined with policy weights
    estimated_cost = -30.0 + (-20.0 * eviction_count)

    return EvictionPlanSummary(
        models_to_evict=frozenset(models_to_evict),
        freed_vram_mb=freed_vram_catalog,
        freed_ram_mb=freed_ram_catalog,
        estimated_cost=estimated_cost,
    )
