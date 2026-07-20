"""
Reclaimable-capacity classifier for eviction failure taxonomy.

Distinguishes structural cannot-fit from transient reclaimable shortfalls when
evaluating whether busy+idle eviction could still admit a placement.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId

from .admission_verdict import evaluate_vram_admission
from .resource_checks import _compute_loading_reservation, resolve_gateway_requirements
from .types import ConstraintFailure

if TYPE_CHECKING:
    from ..types import Gateway, Placement


def can_fit_after_eviction_including_busy(
    gateway: Gateway,
    placement: Placement,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    resource_margins: dict[str, float] | None = None,
) -> tuple[bool, dict[str, int]]:
    """Return True iff reclaimable resources can fit target after eviction.

    Distinguishes transient eviction failure (capacity is reclaimable once loaded
    models can be evicted) from permanent (insufficient reclaimable capacity).
    """
    resolved = resolve_gateway_requirements(gateway, placement)
    if isinstance(resolved, ConstraintFailure):
        return False, {}
    gw_vram_mb, gw_ram_mb = resolved

    margins = resource_margins or {}
    ram_margin_pct = int(margins.get("ram_margin_pct", 3))
    ram_needed = int(gw_ram_mb * (1.0 + ram_margin_pct / 100))

    # Mirror _check_resources / _compute_eviction_plan: loading models consume
    # VRAM/RAM that is NOT reclaimable by eviction.
    vram_reserved = 0
    ram_reserved = 0
    if gateway.loading_models:
        try:
            vram_reserved, ram_reserved, _ = _compute_loading_reservation(
                gateway, placement.model_id, requirements_lookup
            )
        except ValueError:
            return False, {}

    effective_vram_free = gateway.vram_free_mb - vram_reserved
    effective_ram_free = gateway.ram_free_mb - ram_reserved
    admission = evaluate_vram_admission(
        footprint_est_mb=gw_vram_mb,
        vram_free_mb=gateway.vram_free_mb,
        vram_total_mb=gateway.vram_total_mb,
        reserved_mb=vram_reserved,
        resource_margins=margins,
    )
    vram_needed = admission.needed_mb

    reclaimable_vram = effective_vram_free
    reclaimable_ram = effective_ram_free
    for loaded_model_id in gateway.loaded_models:
        measured_vram = gateway.model_measured_vram.get(loaded_model_id)
        catalog_vram, catalog_ram = gateway.get_model_resource_usage(loaded_model_id)
        catalog_req_vram_mb, catalog_req_ram_mb = requirements_lookup(loaded_model_id)
        effective_vram = (
            measured_vram
            if measured_vram is not None
            else max(catalog_req_vram_mb, catalog_vram)
        )
        effective_ram = catalog_req_ram_mb if catalog_req_ram_mb > 0 else catalog_ram
        reclaimable_vram += max(effective_vram, 0)
        reclaimable_ram += max(effective_ram, 0)

    vram_ok = vram_needed <= 0 or reclaimable_vram >= vram_needed
    ram_ok = ram_needed <= 0 or reclaimable_ram >= ram_needed

    diagnostics = {
        "max_freeable_vram": reclaimable_vram,
        "required_vram": vram_needed,
        "vram_deficit_mb": max(0, vram_needed - reclaimable_vram),
        "max_freeable_ram": reclaimable_ram,
        "required_ram": ram_needed,
        "ram_deficit_mb": max(0, ram_needed - reclaimable_ram),
        "vram_reserved_loading": vram_reserved,
        "ram_reserved_loading": ram_reserved,
        **admission.to_payload(),
    }
    return vram_ok and ram_ok, diagnostics


# Private alias for in-package callers that used the underscore name.
_can_fit_after_eviction_including_busy = can_fit_after_eviction_including_busy
