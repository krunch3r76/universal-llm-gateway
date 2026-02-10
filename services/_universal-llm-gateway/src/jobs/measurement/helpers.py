"""
Helper functions for measurement job operations.

Extracted from measurement.py to maintain SLOC under 300.
"""

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...core.gateway_config import GatewayConfig

logger = get_logger(__name__)


async def check_measurement_resources(
    model_id: str,
    gateway_config: "GatewayConfig",
) -> tuple[bool, str | None]:
    """
    Measurement should not block on catalog estimates; it discovers them.

    We log current availability and catalog estimates but never block.

    Args:
        model_id: Model ID to measure
        gateway_config: Gateway configuration with resource guard settings

    Returns:
        Tuple of (can_proceed, error_message)
    """
    try:
        from ...core.resources import resource_tracker

        requirements = resource_tracker.get_model_requirements(model_id)
        required_vram = requirements.get("vram_required_mb")
        required_ram = requirements.get("ram_required_mb")

        system = await resource_tracker.get_system_resources()
        available_vram = system.available_vram_mb
        available_ram = system.available_ram_mb

        # Log for visibility; do not block measurement
        logger.info(
            f"Measurement preflight (non-blocking) for {model_id}: "
            f"catalog_vram={required_vram}MB catalog_ram={required_ram}MB "
            f"available_vram={available_vram}MB available_ram={available_ram}MB"
        )

        warning = None
        if (
            required_vram
            and available_vram is not None
            and available_vram < required_vram
        ):
            warning = (
                f"Catalog VRAM {required_vram}MB exceeds available {available_vram}MB; "
                "continuing measurement to discover actual fit"
            )
        elif (
            required_ram and available_ram is not None and available_ram < required_ram
        ):
            warning = (
                f"Catalog RAM {required_ram}MB exceeds available {available_ram}MB; "
                "continuing measurement to discover actual fit"
            )

        return True, warning
    except Exception as e:
        logger.error(f"Resource probe failed for {model_id}: {e}")
        # Do not block measurement on probe failure
        return True, f"Resource probe error: {e}"


async def update_catalog_with_results(
    model_id: str,
    mode: str,
    results: dict[str, dict[str, Any]],
    emit_log: callable,
    use_static: bool = False,
) -> None:
    """
    Update catalog with measurement results.

    For use_static=True: Skip catalog write (CLI handles it on host)
    For use_static=False: Update local/dynamic catalog (unchanged)

    Args:
        model_id: Model identifier
        mode: Measurement mode (gpu, cpu, auto)
        results: Profile results to write to catalog
        emit_log: Logging callback for job progress
        use_static: If True, skip (CLI handles static writes on host filesystem)
    """
    if use_static:
        # Static catalog writes are handled by CLI (host filesystem access)
        # Gateway has read-only /app/config mount
        emit_log("📝 Static catalog update skipped (CLI will write to host)")
        return

    from inference_djinn.catalog.local_config import load_local_catalog

    from ..context_detection import (
        determine_activated_contexts,
        update_local_catalog_contexts,
        update_local_catalog_profile,
    )

    emit_log("Updating local catalog...")

    try:
        # Check if model exists in local catalog before attempting updates
        local_catalog = load_local_catalog()
        local_models = local_catalog.get("models", {})
        if model_id not in local_models:
            # Model only in static catalog - local updates not applicable
            # (CLI handles static catalog writes on host filesystem)
            emit_log("  → Model not in local catalog (static-only), skipping")
            return

        # Update individual profile measurements
        for ctx_str, profile in results.items():
            if profile.get("error"):
                continue

            # Safety margin is now applied during measurement phase:
            # - First hybrid context: -2 margin (at edge of fitting)
            # - Subsequent hybrid contexts: no margin (comfortable fit)
            # - Full GPU (-1): no margin needed
            # Write measured values directly to catalog
            measured_layers = profile.get("n_gpu_layers")

            success = update_local_catalog_profile(
                model_id=model_id,
                context=int(ctx_str),
                vram_mb=profile.get("vram_mb", 0),
                ram_mb=profile.get("ram_mb", 0),
                n_gpu_layers=measured_layers,
            )
            if success:
                emit_log(f"  ✅ Updated profile@{ctx_str} in local catalog")
            else:
                emit_log(f"  ⚠️ Failed to update {ctx_str} in local catalog")

        # Determine and set activated contexts
        gpu_contexts, cpu_contexts, reason = determine_activated_contexts(results, mode)

        if reason:
            emit_log(f"  → Activating: {reason}")

        if gpu_contexts or cpu_contexts:
            success = update_local_catalog_contexts(
                model_id, gpu_contexts, cpu_contexts
            )
            if success:
                emit_log("  ✅ Updated activated contexts in local catalog")
            else:
                emit_log("  ⚠️ Failed to update activated contexts in local catalog")

    except Exception as e:
        emit_log(f"  ⚠️ Catalog update failed: {e}")
