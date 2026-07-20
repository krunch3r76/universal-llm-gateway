"""
Greedy eviction victim selection and hardware VRAM freeable correction.

Picks a minimum idle-model set to free placement footprints, then corrects
catalog freeable estimates against hardware reserves when present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .eviction_hysteresis import HysteresisResult
from .types import EvictionPlanSummary

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)

_MIN_NON_EVICTABLE_VRAM_RESERVE_MB = 512
_NON_EVICTABLE_VRAM_RESERVE_RATIO = 0.02


def compute_non_evictable_vram_reserve_mb(vram_total_mb: int) -> int:
    """Return conservative VRAM reserve that eviction must not assume freeable."""
    if vram_total_mb <= 0:
        return _MIN_NON_EVICTABLE_VRAM_RESERVE_MB
    return max(
        _MIN_NON_EVICTABLE_VRAM_RESERVE_MB,
        int(vram_total_mb * _NON_EVICTABLE_VRAM_RESERVE_RATIO),
    )


def select_eviction_victims(
    gateway: Gateway,
    placement: Placement,
    *,
    hyst: HysteresisResult,
    gw_vram_mb: int,
    gw_ram_mb: int,
    effective_vram_free: int,
    effective_ram_free: int,
    resource_margins: dict[str, float] | None,
    routing_key_tracker: RoutingKeyTracker | None,
    gw_keys_in_flight: set[str] | None,
) -> EvictionPlanSummary | None:
    """Greedy minimum idle-model eviction with optional hardware freeable correction."""
    evictable_with_resources: list[tuple[ModelId, int, int]] = []
    for model_id in hyst.evictable:
        measured_vram = gateway.model_measured_vram.get(model_id)
        catalog_vram, ram_usage = gateway.get_model_resource_usage(model_id)
        vram_usage = measured_vram if measured_vram is not None else catalog_vram
        src = "measured" if measured_vram is not None else "catalog"
        logger.debug(
            f"Candidate {model_id}: vram={vram_usage}MB ({src}), ram={ram_usage}MB"
        )
        evictable_with_resources.append((model_id, vram_usage, ram_usage))

    _margins = resource_margins or {}
    vram_margin_pct = _margins.get("vram_margin_pct", 5)
    vram_headroom_mb = int(_margins.get("vram_headroom_mb", 2048))
    ram_margin_pct = _margins.get("ram_margin_pct", 3)

    vram_pct = int(gw_vram_mb * (1.0 + vram_margin_pct / 100))
    vram_target = (vram_pct + vram_headroom_mb) if gw_vram_mb > 0 else 0
    ram_target = int(gw_ram_mb * (1.0 + ram_margin_pct / 100))

    evictable_with_resources.sort(key=lambda x: (-x[1], -x[2]))

    catalog_freed_vram = 0
    freed_ram_catalog = 0
    models_to_evict: list[ModelId] = []

    for model_id, vram_usage, ram_usage in evictable_with_resources:
        models_to_evict.append(model_id)
        catalog_freed_vram += vram_usage
        freed_ram_catalog += ram_usage
        vram_covered = gw_vram_mb <= 0 or (
            effective_vram_free + catalog_freed_vram >= vram_target
        )
        ram_covered = gw_ram_mb <= 0 or (
            effective_ram_free + freed_ram_catalog >= ram_target
        )
        if vram_covered and ram_covered:
            break

    corrected_freed_vram = catalog_freed_vram
    hardware_used_vram_mb: int | None = None
    non_evictable_vram_reserve_mb = 0
    hardware_correction_applied = False

    if gateway.vram_total_mb > 0 and set(models_to_evict) == set(gateway.loaded_models):
        hardware_used_vram_mb = max(0, gateway.vram_total_mb - gateway.vram_free_mb)
        non_evictable_vram_reserve_mb = compute_non_evictable_vram_reserve_mb(
            gateway.vram_total_mb
        )
        hardware_freeable_upper_bound = max(
            0, hardware_used_vram_mb - non_evictable_vram_reserve_mb
        )

        if hardware_freeable_upper_bound > corrected_freed_vram:
            logger.info(
                f"📊 Hardware correction for {gateway.name}: "
                f"catalog freeable={catalog_freed_vram}MB, "
                f"hardware_used={hardware_used_vram_mb}MB, "
                f"reserve={non_evictable_vram_reserve_mb}MB, "
                f"hardware_upper_bound={hardware_freeable_upper_bound}MB. "
                f"Applying corrected freeable VRAM estimate."
            )
            corrected_freed_vram = hardware_freeable_upper_bound
            hardware_correction_applied = True

    total_vram = effective_vram_free + corrected_freed_vram
    total_ram = effective_ram_free + freed_ram_catalog

    logger.debug(
        f"Eviction estimate ({len(models_to_evict)}/{len(gateway.loaded_models)} "
        f"models): available_after={total_vram}MB VRAM, {total_ram}MB RAM "
        f"(effective_free={effective_vram_free}MB + "
        f"catalog_freed={catalog_freed_vram}MB, "
        f"corrected_freed={corrected_freed_vram}MB)"
    )
    logger.debug(
        f"Eviction calculation: "
        f"catalog_freed={catalog_freed_vram}MB VRAM, "
        f"corrected_freed={corrected_freed_vram}MB VRAM, "
        f"hardware_used={hardware_used_vram_mb}MB, "
        f"reserve={non_evictable_vram_reserve_mb}MB, "
        f"correction_applied={hardware_correction_applied}, "
        f"total_available={total_vram}MB VRAM (need {gw_vram_mb}MB), "
        f"total_available={total_ram}MB RAM (need {gw_ram_mb}MB)"
    )

    if gw_vram_mb > 0 and total_vram < vram_target:
        logger.warning(
            f"❌ EVICTION FAILED for {placement.model_id}: "
            f"Insufficient VRAM - need {vram_target}MB (margin+headroom), "
            f"can only get {total_vram}MB (free: {gateway.vram_free_mb}MB + "
            f"freeable: {corrected_freed_vram}MB from {len(models_to_evict)} models)"
        )
        return None
    if gw_ram_mb > 0 and total_ram < ram_target:
        logger.warning(
            f"❌ EVICTION FAILED for {placement.model_id}: "
            f"Insufficient RAM - need {ram_target}MB (incl. margin), "
            f"can only get {total_ram}MB (free: {gateway.ram_free_mb}MB + "
            f"freeable: {freed_ram_catalog}MB from {len(models_to_evict)} models)"
        )
        return None

    logger.info(
        f"✅ EVICTION PLAN for {placement.model_id}: "
        f"evict {[str(m) for m in models_to_evict]} → "
        f"free {corrected_freed_vram}MB VRAM, {freed_ram_catalog}MB RAM "
        f"(catalog={catalog_freed_vram}MB, correction={hardware_correction_applied})"
    )

    if routing_key_tracker is not None and gw_keys_in_flight is not None:
        in_flight_violations = [
            mid for mid in models_to_evict if mid.routing_key in gw_keys_in_flight
        ]
        if in_flight_violations:
            logger.error(
                f"🚨 INVARIANT VIOLATION: eviction plan for {placement.model_id} "
                f"includes in-flight models {[str(m) for m in in_flight_violations]} "
                f"on {gateway.name}. Aborting eviction to protect active generation."
            )
            return None

    eviction_count = len(models_to_evict)
    estimated_cost = -30.0 + (-20.0 * eviction_count)

    return EvictionPlanSummary(
        models_to_evict=frozenset(models_to_evict),
        freed_vram_mb=corrected_freed_vram,
        freed_ram_mb=freed_ram_catalog,
        estimated_cost=estimated_cost,
        catalog_freed_vram_mb=catalog_freed_vram,
        hardware_used_vram_mb=hardware_used_vram_mb,
        non_evictable_vram_reserve_mb=non_evictable_vram_reserve_mb,
        hardware_correction_applied=hardware_correction_applied,
        trigger_model_id=str(placement.model_id),
        cooldown_protected_count=hyst.cooldown_protected_count,
        demand_protected_count=hyst.demand_protected_count,
        escape_hatch_used=hyst.escape_hatch_used,
        escape_reason=hyst.escape_reason,
        escape_cooldown_remaining_s=hyst.escape_cooldown_remaining_s,
        escape_model_id=hyst.escape_model_id,
        cooldown_override_pending=hyst.cooldown_override_pending,
        cooldown_override_victim_id=hyst.cooldown_override_victim_id,
        cooldown_override_remaining_s=hyst.cooldown_override_remaining_s,
    )
