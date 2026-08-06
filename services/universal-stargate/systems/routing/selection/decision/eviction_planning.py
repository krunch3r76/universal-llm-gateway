"""
Eviction planning for feasibility evaluation.

Computes which models to evict to make room for a new model.
Supports eviction hysteresis (cooldown + demand-aware protection).
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .busy_view import actually_busy_models, idle_models
from .eviction_cooldown_policy import EvictionRequestClass
from .eviction_hysteresis import filter_evictable_with_hysteresis
from .eviction_victim_select import (
    compute_non_evictable_vram_reserve_mb,
    select_eviction_victims,
)
from .resource_checks import _compute_loading_reservation, resolve_gateway_requirements
from .types import ConstraintFailure, EvictionPlanAbort, EvictionPlanSummary

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)

# Re-export for callers/tests that historically imported from this module.
_compute_non_evictable_vram_reserve_mb = compute_non_evictable_vram_reserve_mb


def _compute_eviction_plan(
    gateway: "Gateway",
    placement: "Placement",
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    routing_key_tracker: "RoutingKeyTracker | None" = None,
    eviction_cooldown_s: float = 120.0,
    has_demand: Callable[[str], bool] | None = None,
    resource_margins: dict[str, float] | None = None,
    eviction_request_class: EvictionRequestClass = EvictionRequestClass.REQUIRED,
) -> EvictionPlanSummary | EvictionPlanAbort | None:
    """Compute an eviction plan that frees enough VRAM/RAM to load a model
    with full runtime headroom margins.

    The deficit includes percentage margins and an absolute headroom floor
    matching _check_resources, so the eviction plan frees enough for the
    post-eviction resource check to pass without a wasted eviction cycle.
    """
    resolved = resolve_gateway_requirements(gateway, placement)
    if isinstance(resolved, ConstraintFailure):
        logger.error(f"Eviction planning blocked: {resolved.reason}")
        return None

    gw_vram_mb, gw_ram_mb = resolved

    logger.info(
        f"🔍 EVICTION EVAL for {placement.model_id} on {gateway.name}: "
        f"Need {gw_vram_mb}MB VRAM, {gw_ram_mb}MB RAM | "
        f"Currently available: {gateway.vram_free_mb}MB VRAM, "
        f"{gateway.ram_free_mb}MB RAM | "
        f"Loaded models: {list(gateway.loaded_models)}"
    )

    vram_reserved = 0
    ram_reserved = 0
    if gateway.loading_models:
        try:
            vram_reserved, ram_reserved, _ = _compute_loading_reservation(
                gateway, placement.model_id, requirements_lookup
            )
        except ValueError as e:
            logger.error(f"Eviction planning blocked: {e}")
            return None

    effective_vram_free = gateway.vram_free_mb - vram_reserved
    effective_ram_free = gateway.ram_free_mb - ram_reserved

    if effective_vram_free < 0:
        logger.debug(
            f"Gateway {gateway.name} VRAM overcommitted during eviction planning: "
            f"effective={effective_vram_free}MB (hardware={gateway.vram_free_mb}MB, "
            f"reserved={vram_reserved}MB)"
        )

    if routing_key_tracker is None:
        logger.debug("No routing_key_tracker provided - trusting busy_models telemetry")
    gw_keys_in_flight = (
        routing_key_tracker.get_routing_keys_in_flight(gateway.name)
        if routing_key_tracker
        else None
    )
    logger.debug(
        f"Per-gateway in-flight routing keys for {gateway.name}: {gw_keys_in_flight}"
    )

    busy_set = actually_busy_models(
        gateway, routing_key_tracker, gw_keys_in_flight
    )
    idle = idle_models(gateway, routing_key_tracker, gw_keys_in_flight)
    stale_busy_count = sum(
        1
        for mid in gateway.loaded_models
        if mid in gateway.busy_models and mid not in busy_set
    )

    logger.debug(
        f"Eviction planning for {placement.model_id}: "
        f"loaded={list(gateway.loaded_models)}, "
        f"busy={list(gateway.busy_models)}, "
        f"actually_busy={list(busy_set)}, "
        f"idle={idle}"
    )

    if not idle:
        logger.debug("No idle models available for eviction")
        return None

    target_model_id = placement.model_id
    evictable = [mid for mid in idle if mid != target_model_id]

    logger.debug(
        f"After filtering target variants: evictable={evictable}, "
        f"target_key={target_model_id}"
    )
    logger.info(
        f"🔍 EVICTION CANDIDATES: {len(evictable)} evictable models "
        f"(after filtering {len(gateway.busy_models)} busy, "
        f"{stale_busy_count} stale-busy reclassified idle, "
        f"{len(idle) - len(evictable)} same routing key)"
    )

    if not evictable:
        logger.debug("No evictable models after filtering")
        return None

    hyst = filter_evictable_with_hysteresis(
        gateway,
        evictable,
        eviction_cooldown_s=eviction_cooldown_s,
        has_demand=has_demand,
        eviction_request_class=eviction_request_class,
    )
    if hyst is None:
        return None

    return select_eviction_victims(
        gateway,
        placement,
        hyst=hyst,
        gw_vram_mb=gw_vram_mb,
        gw_ram_mb=gw_ram_mb,
        effective_vram_free=effective_vram_free,
        effective_ram_free=effective_ram_free,
        resource_margins=resource_margins,
        routing_key_tracker=routing_key_tracker,
        gw_keys_in_flight=gw_keys_in_flight,
    )
