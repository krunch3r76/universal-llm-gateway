"""
Resource checking for feasibility evaluation.

Handles resource availability checks including loading model reservations.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .types import ConstraintFailure

if TYPE_CHECKING:
    from ..types import Gateway, Placement

logger = get_logger(__name__)


def _compute_loading_reservation(
    gateway: "Gateway",
    target_model: ModelId,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
) -> tuple[int, int, list[str]]:
    """
    Compute resources reserved by loading models (excluding target).

    Invariant: ∀ model ∈ loading_models, model ≠ target_model ⟹ counted

    Args:
        gateway: Gateway with loading_models set
        target_model: Model being evaluated (exclude from reservation)
        requirements_lookup: Function to get (vram_mb, ram_mb) for ModelId

    Returns:
        (vram_reserved_mb, ram_reserved_mb, debug_details)

    Raises:
        ValueError: If loading model has unknown requirements (fail-fast)
    """
    vram_reserved = 0
    ram_reserved = 0
    loading_details: list[str] = []

    # Only reserve for loading models OTHER than target (prevent double-count)
    other_loading = gateway.loading_models - {target_model}

    for loading_model_id in other_loading:
        model_vram, model_ram = requirements_lookup(loading_model_id)

        # Fail-fast if unknown (missing requirements for loading model is a bug)
        if model_vram == 0 and model_ram == 0:
            raise ValueError(
                f"Missing requirements for loading model {loading_model_id} "
                f"on {gateway.name}. This indicates a catalog/WebSocket sync issue."
            )

        vram_reserved += model_vram
        ram_reserved += model_ram
        loading_details.append(f"{loading_model_id}({model_vram}MB VRAM)")

    return vram_reserved, ram_reserved, loading_details


def _check_resources(
    gateway: "Gateway",
    placement: "Placement",
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    config: dict | None = None,
) -> tuple[bool, ConstraintFailure | None]:
    """
    Check if gateway has sufficient resources.

    Invariant: ∀ check, effective_free = hardware_free - reserved_by_loading

    Hybrid models (is_gpu=True with ram_mb > 0) need BOTH VRAM and RAM.
    Pure GPU models need only VRAM. Pure CPU models need only RAM.

    Args:
        gateway: Gateway to check resources on
        placement: Model placement requirements
        requirements_lookup: MANDATORY function to look up (vram_mb, ram_mb)
                            for loading models (in-memory, no I/O)
        config: Optional config dict for resource margins
    """
    logger.info(
        f"🔍 RESOURCE CHECK START: {placement.model_id} on {gateway.name} | "
        f"Placement requires: VRAM={placement.vram_mb}MB, RAM={placement.ram_mb}MB | "
        f"Gateway available: VRAM={gateway.vram_free_mb}MB, "
        f"RAM={gateway.ram_free_mb}MB | "
        f"Gateway loaded: {list(gateway.loaded_models)}, "
        f"loading: {list(gateway.loading_models)}"
    )

    ram_margin = 1.03  # 3% safety margin
    # VRAM margin: configurable, disabled by default (rely on catalog accuracy)
    # Enable only if catalog systematically underestimates VRAM requirements
    vram_margin_config = (
        config.get("resource_margins", {}).get("vram_margin") if config else None
    )
    vram_margin = (
        vram_margin_config if vram_margin_config is not None else 1.0
    )  # Default: disabled

    ram_needed = int(placement.ram_mb * ram_margin)
    vram_needed = int(placement.vram_mb * vram_margin)

    # NEW: Calculate resources reserved by loading models (exclude target)
    vram_reserved = 0
    ram_reserved = 0
    loading_details: list[str] = []

    if gateway.loading_models:
        try:
            vram_reserved, ram_reserved, loading_details = _compute_loading_reservation(
                gateway, placement.model_id, requirements_lookup
            )

            if vram_reserved > 0 or ram_reserved > 0:
                logger.info(
                    f"📊 Resource reservation on {gateway.name}: "
                    f"{vram_reserved}MB VRAM, {ram_reserved}MB RAM "
                    f"reserved by {len(loading_details)} loading model(s): "
                    f"{loading_details}"
                )
        except ValueError as e:
            # Fail-fast: missing requirements for loading model
            logger.error(f"❌ {e}")
            return False, ConstraintFailure(
                constraint="loading_model_requirements_missing",
                reason=str(e),
                details={"loading_models": [str(m) for m in gateway.loading_models]},
            )

    # Effective free = hardware - reserved
    # Note: Can be negative if loading models exceed current hardware free
    # (e.g., model loading, GPU allocation in progress)
    effective_vram_free = gateway.vram_free_mb - vram_reserved
    effective_ram_free = gateway.ram_free_mb - ram_reserved

    # Log diagnostic if effective free is negative (loading models exceed hardware free)
    if effective_vram_free < 0:
        logger.info(
            f"⚠️ Gateway {gateway.name} VRAM temporarily overcommitted: "
            f"hardware_free={gateway.vram_free_mb}MB, "
            f"reserved_by_loading={vram_reserved}MB, "
            f"effective={effective_vram_free}MB (model loading in progress)"
        )
    if effective_ram_free < 0:
        logger.info(
            f"⚠️ Gateway {gateway.name} RAM temporarily overcommitted: "
            f"hardware_free={gateway.ram_free_mb}MB, "
            f"reserved_by_loading={ram_reserved}MB, "
            f"effective={effective_ram_free}MB (model loading in progress)"
        )

    # Check VRAM with loading reservation
    if placement.vram_mb > 0 and effective_vram_free < vram_needed:
        margin_info = (
            f" (+ {(vram_margin - 1) * 100:.0f}% margin)" if vram_margin > 1.0 else ""
        )
        reserved_info = (
            f" ({vram_reserved}MB reserved by {len(loading_details)} loading model(s))"
            if vram_reserved > 0
            else ""
        )

        logger.warning(
            f"❌ RESOURCE CHECK FAILED (VRAM): {placement.model_id} "
            f"on {gateway.name} | "
            f"Need {vram_needed}MB{margin_info}, "
            f"Available {effective_vram_free}MB "
            f"(hardware={gateway.vram_free_mb}MB{reserved_info})"
        )

        return False, ConstraintFailure(
            constraint="has_enough_vram",
            reason=(
                f"Insufficient VRAM: {effective_vram_free}MB effective free "
                f"(hardware: {gateway.vram_free_mb}MB{reserved_info}) "
                f"< {vram_needed}MB needed{margin_info}"
            ),
            details={
                "vram_free_hardware": gateway.vram_free_mb,
                "vram_reserved_loading": vram_reserved,
                "vram_free_effective": effective_vram_free,
                "vram_needed": vram_needed,
                "vram_base": placement.vram_mb,
                "vram_margin": vram_margin,
                "loading_models": [str(m) for m in gateway.loading_models],
                "loading_details": loading_details,
            },
        )

    # Check RAM with loading reservation
    if placement.ram_mb > 0 and effective_ram_free < ram_needed:
        reserved_info = (
            f" ({ram_reserved}MB reserved by {len(loading_details)} loading model(s))"
            if ram_reserved > 0
            else ""
        )

        logger.warning(
            f"❌ RESOURCE CHECK FAILED (RAM): {placement.model_id} on {gateway.name} | "
            f"Need {ram_needed}MB (+ 10% margin), "
            f"Available {effective_ram_free}MB "
            f"(hardware={gateway.ram_free_mb}MB{reserved_info})"
        )

        return False, ConstraintFailure(
            constraint="has_enough_ram",
            reason=(
                f"Insufficient RAM: {effective_ram_free}MB effective free "
                f"(hardware: {gateway.ram_free_mb}MB{reserved_info}) "
                f"< {ram_needed}MB needed"
            ),
            details={
                "ram_free_hardware": gateway.ram_free_mb,
                "ram_reserved_loading": ram_reserved,
                "ram_free_effective": effective_ram_free,
                "ram_needed": ram_needed,
                "loading_models": [str(m) for m in gateway.loading_models],
                "loading_details": loading_details,
            },
        )

    logger.info(
        f"✅ RESOURCE CHECK PASSED: {placement.model_id} on {gateway.name} | "
        f"VRAM: {effective_vram_free}MB available >= {vram_needed}MB needed, "
        f"RAM: {effective_ram_free}MB available >= {ram_needed}MB needed"
    )
    return True, None
