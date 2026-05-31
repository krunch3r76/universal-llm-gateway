"""
Eviction planning for feasibility evaluation.

Computes which models to evict to make room for a new model.
Supports eviction hysteresis (cooldown + demand-aware protection).
"""

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .resource_checks import _compute_loading_reservation, resolve_gateway_requirements
from .types import ConstraintFailure, EvictionPlanSummary

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)

_MIN_NON_EVICTABLE_VRAM_RESERVE_MB = 512
_NON_EVICTABLE_VRAM_RESERVE_RATIO = 0.02


def _compute_non_evictable_vram_reserve_mb(vram_total_mb: int) -> int:
    """Return conservative VRAM reserve that eviction must not assume freeable."""
    if vram_total_mb <= 0:
        return _MIN_NON_EVICTABLE_VRAM_RESERVE_MB
    return max(
        _MIN_NON_EVICTABLE_VRAM_RESERVE_MB,
        int(vram_total_mb * _NON_EVICTABLE_VRAM_RESERVE_RATIO),
    )


def _is_model_actually_busy(
    gateway: "Gateway",
    model_id: ModelId,
    routing_key_tracker: "RoutingKeyTracker | None",
    gw_keys_in_flight: set[str] | None = None,
) -> bool:
    """Return True iff the model has verified in-flight requests.

    INVARIANT: ∀ model_id: tracker_in_flight(model_id, gateway) ⟹ busy(model_id)

    The routing_key_tracker is checked first because it is the master's
    authoritative record of requests it dispatched and not yet completed.
    Telemetry (busy_models) is a best-effort hint from the edge; it can be
    momentarily stale (e.g., between generation batches) and MUST NOT be
    used to override a positive tracker signal.

    Decision matrix:
      tracker has keys   → busy (regardless of telemetry)
      no tracker         → telemetry alone decides
      tracker, no keys   → idle (telemetry "busy" treated as stale)
    """
    if routing_key_tracker is not None:
        keys_in_flight = (
            gw_keys_in_flight
            if gw_keys_in_flight is not None
            else routing_key_tracker.get_routing_keys_in_flight(gateway.name)
        )
        if model_id.routing_key in keys_in_flight:
            return True
        # Tracker is authoritative: no in-flight keys → model is idle from
        # the master's perspective. If telemetry still says busy, it is stale.
        if model_id in gateway.busy_models:
            logger.info(
                f"📊 Stale busy_models detected: {model_id} on {gateway.name} "
                f"is busy per telemetry but idle per routing tracker"
            )
        return False

    # No routing tracker (standalone/edge mode): fall back to telemetry.
    return model_id in gateway.busy_models


def _compute_eviction_plan(
    gateway: "Gateway",
    placement: "Placement",
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    routing_key_tracker: "RoutingKeyTracker | None" = None,
    eviction_cooldown_s: float = 120.0,
    has_demand: Callable[[str], bool] | None = None,
    resource_margins: dict[str, float] | None = None,
) -> EvictionPlanSummary | None:
    """Compute an eviction plan that frees enough VRAM/RAM to load a model
    with full runtime headroom margins.

    The deficit includes percentage margins and an absolute headroom floor
    matching _check_resources, so the eviction plan frees enough for the
    post-eviction resource check to pass without a wasted eviction cycle.

    Args:
        gateway: Gateway to compute eviction plan for.
        placement: Model placement requirements.
        requirements_lookup: Function to look up (vram_mb, ram_mb) for loading
            models (in-memory, no I/O).
        routing_key_tracker: Tracker for in-flight routing keys (eviction
            protection). Required for Master mode.
        eviction_cooldown_s: Minimum seconds a model must stay loaded before
            becoming evictable. Prevents model thrashing livelock.
        has_demand: Callback returning True if routing queue has waiters for
            a routing_key. Models with demand are protected from eviction.
        resource_margins: Margin config dict (vram_margin_pct, vram_headroom_mb,
            ram_margin_pct). Defaults applied if None.

    Returns None if eviction cannot provide enough resources.
    """
    resolved = resolve_gateway_requirements(gateway, placement)
    if isinstance(resolved, ConstraintFailure):
        logger.error(f"Eviction planning blocked: {resolved.reason}")
        return None

    gw_vram_mb, gw_ram_mb = resolved

    # Log entry point with full context
    logger.info(
        f"🔍 EVICTION EVAL for {placement.model_id} on {gateway.name}: "
        f"Need {gw_vram_mb}MB VRAM, {gw_ram_mb}MB RAM | "
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

    # Get in-flight keys scoped to this gateway. Global in-flight keys are not
    # relevant for a per-gateway eviction decision.
    gw_keys_in_flight: set[str] | None = None
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

    actually_busy_models = {
        mid
        for mid in gateway.loaded_models
        if _is_model_actually_busy(gateway, mid, routing_key_tracker, gw_keys_in_flight)
    }

    # Get idle models (loaded but not actually busy)
    idle_models = [
        mid
        for mid in gateway.loaded_models
        if mid not in actually_busy_models and mid not in gateway.loading_models
    ]
    stale_busy_count = sum(
        1
        for mid in gateway.loaded_models
        if mid in gateway.busy_models and mid not in actually_busy_models
    )

    logger.debug(
        f"Eviction planning for {placement.model_id}: "
        f"loaded={list(gateway.loaded_models)}, "
        f"busy={list(gateway.busy_models)}, "
        f"actually_busy={list(actually_busy_models)}, "
        f"idle={idle_models}"
    )

    if not idle_models:
        logger.debug("No idle models available for eviction")
        return None

    # Filter out variants of target model
    target_model_id = placement.model_id
    evictable = [mid for mid in idle_models if mid != target_model_id]

    logger.debug(
        f"After filtering target variants: evictable={evictable}, "
        f"target_key={target_model_id}"
    )

    logger.info(
        f"🔍 EVICTION CANDIDATES: {len(evictable)} evictable models "
        f"(after filtering {len(gateway.busy_models)} busy, "
        f"{stale_busy_count} stale-busy reclassified idle, "
        f"{len(idle_models) - len(evictable)} same routing key)"
    )

    if not evictable:
        logger.debug("No evictable models after filtering")
        return None

    # ---------------------------------------------------------------
    # Hysteresis: cooldown + demand-aware filtering
    # ---------------------------------------------------------------
    now = time.monotonic()
    cooldown_protected: list[ModelId] = []
    past_cooldown: list[ModelId] = []
    for mid in evictable:
        elapsed = now - gateway.model_loaded_at.get(mid, 0.0)
        if elapsed < eviction_cooldown_s:
            cooldown_protected.append(mid)
        else:
            past_cooldown.append(mid)

    if cooldown_protected:
        logger.info(
            f"🛡️ Cooldown protection: {len(cooldown_protected)} models "
            f"within {eviction_cooldown_s}s window"
        )

    evictable = past_cooldown

    demand_protected: list[ModelId] = []
    if has_demand is not None and evictable:
        still_evictable: list[ModelId] = []
        for mid in evictable:
            if has_demand(mid.routing_key):
                demand_protected.append(mid)
            else:
                still_evictable.append(mid)
        if demand_protected:
            logger.info(
                f"🛡️ Demand protection: {len(demand_protected)} models "
                f"have queued consumers"
            )
        evictable = still_evictable

    # Escape hatch: if both filters emptied the list but candidates exist,
    # evict the least-harmful candidate to prevent starvation.
    escape_hatch_used = False
    escape_reason: str | None = None
    escape_cooldown_remaining_s: float | None = None
    escape_model_id: str | None = None

    if not evictable and (cooldown_protected or demand_protected):
        # Prefer demand-only protected (less disruptive), then oldest loaded
        all_protected = demand_protected + sorted(
            cooldown_protected,
            key=lambda m: gateway.model_loaded_at.get(m, 0.0),
        )
        escape_candidate = all_protected[0]
        evictable = [escape_candidate]
        escape_hatch_used = True
        escape_reason = "demand" if escape_candidate in demand_protected else "cooldown"
        remaining_cooldown = eviction_cooldown_s - (
            now - gateway.model_loaded_at.get(escape_candidate, 0.0)
        )
        escape_cooldown_remaining_s = max(0.0, remaining_cooldown)
        escape_model_id = str(escape_candidate)
        logger.warning(
            f"⚠️ Escape hatch: all candidates protected, evicting "
            f"{escape_candidate} (reason={escape_reason}, "
            f"cooldown_remaining={escape_cooldown_remaining_s:.1f}s)"
        )

    if not evictable:
        logger.debug("No evictable models after hysteresis filtering")
        return None

    # Collect effective VRAM per candidate (measured preferred over catalog)
    evictable_with_resources: list[tuple[ModelId, int, int]] = []
    for model_id in evictable:
        measured_vram = gateway.model_measured_vram.get(model_id)
        catalog_vram, ram_usage = gateway.get_model_resource_usage(model_id)
        vram_usage = measured_vram if measured_vram is not None else catalog_vram
        src = "measured" if measured_vram is not None else "catalog"
        logger.debug(
            f"Candidate {model_id}: vram={vram_usage}MB ({src}), ram={ram_usage}MB"
        )
        evictable_with_resources.append((model_id, vram_usage, ram_usage))

    # Compute full target including margins and headroom floor, matching
    # _check_resources so the post-eviction resource check passes.
    _margins = resource_margins or {}
    vram_margin_pct = _margins.get("vram_margin_pct", 5)
    vram_headroom_mb = int(_margins.get("vram_headroom_mb", 2048))
    ram_margin_pct = _margins.get("ram_margin_pct", 3)

    vram_pct = int(gw_vram_mb * (1.0 + vram_margin_pct / 100))
    vram_target = (vram_pct + vram_headroom_mb) if gw_vram_mb > 0 else 0
    ram_target = int(gw_ram_mb * (1.0 + ram_margin_pct / 100))

    # Greedy minimum eviction: largest VRAM first → fewest models evicted
    # ∀ plan: |models_to_evict| is minimal — stop as soon as freed ≥ deficit
    evictable_with_resources.sort(key=lambda x: (-x[1], -x[2]))

    catalog_freed_vram = 0
    freed_ram_catalog = 0
    models_to_evict = []

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

    # Hardware correction: if we are evicting ALL currently loaded models, catalog-based
    # freeable VRAM can be a material underestimate on AMD/HIP deployments where
    # per-process measurement may be unavailable.
    #
    # To avoid false-positive feasibility, cap correction by a non-evictable reserve:
    # freeable_vram ≤ hardware_used_vram - reserve.
    #
    # ∀ correction:
    #   corrected_freed_vram = max(catalog_freed_vram, hardware_freeable_upper_bound)
    #   where hardware_freeable_upper_bound = max(0, (total - free) - reserve)
    if gateway.vram_total_mb > 0 and set(models_to_evict) == set(gateway.loaded_models):
        hardware_used_vram_mb = max(0, gateway.vram_total_mb - gateway.vram_free_mb)
        non_evictable_vram_reserve_mb = _compute_non_evictable_vram_reserve_mb(
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

    # Effective free + VRAM freed by evicting loaded models.
    # Non-model VRAM consumers (driver overhead, reserved memory) are implicitly
    # accounted for because they reduce gateway.vram_free_mb but are never "freed".
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

    # Check BOTH resources against margin+headroom targets so post-eviction
    # _check_resources will pass.
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

    # Belt-and-suspenders: INVARIANT ∀ mid ∈ models_to_evict: ¬in_flight(mid)
    # _is_model_actually_busy should have already excluded in-flight models;
    # this guard catches any future regression in that classification.
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

    # Calculate eviction cost
    eviction_count = len(models_to_evict)
    # TODO: Replace with a configurable, policy-driven cost model.
    # For now, a simple heuristic: base penalty + penalty per evicted model.
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
        cooldown_protected_count=len(cooldown_protected),
        demand_protected_count=len(demand_protected),
        escape_hatch_used=escape_hatch_used,
        escape_reason=escape_reason,
        escape_cooldown_remaining_s=escape_cooldown_remaining_s,
        escape_model_id=escape_model_id,
    )
