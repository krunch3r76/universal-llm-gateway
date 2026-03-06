"""
Helper functions for measurement job operations.

Extracted from measurement.py to maintain SLOC under 300.
"""

from collections.abc import Callable
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
    emit_log: Callable[[str], None],
    use_static: bool = False,
    loader_updates: dict[str, Any] | None = None,
) -> None:
    """
    Update local catalog (~/.gateway/catalog/) with measurement results.

    Two write paths:
    - use_static=True: Skip (CLI dual-writes to both catalogs on host)
    - use_static=False: Update local catalog. Tries legacy inference_djinn catalog
      first; falls back to per-file catalog for models added via the new system.

    Args:
        model_id: Model identifier
        mode: Measurement mode (gpu, cpu, auto)
        results: Profile results to write to catalog
        emit_log: Logging callback for job progress
        use_static: If True, skip (CLI handles dual-write on host filesystem)
        loader_updates: Loader params to persist (e.g. gpu_memory_utilization)
    """
    if use_static:
        emit_log("📝 Catalog update skipped (CLI will dual-write to host)")
        return

    from inference_djinn.catalog.local_config import (
        load_local_catalog,
        save_local_catalog,
    )

    from ..context_detection import (
        determine_activated_contexts,
        update_local_catalog_contexts,
        update_local_catalog_profile,
    )

    emit_log("Updating local catalog...")

    try:
        local_catalog = load_local_catalog()
        local_models = local_catalog.get("models", {})
        if model_id not in local_models:
            # Model is not in the legacy catalog — try the per-file catalog.
            # ∀ model added via model_manager generate: lives in per-file catalog only.
            emit_log("  → Not in legacy catalog; writing to per-file catalog")
            await _write_per_file_catalog_results(
                model_id, mode, results, emit_log, loader_updates
            )
            return

        # Persist loader-level params (e.g. gpu_memory_utilization for vLLM)
        if loader_updates:
            model_entry = local_models[model_id]
            loader = model_entry.setdefault("loader", {})
            for key, value in loader_updates.items():
                loader.setdefault(key, value)
            save_local_catalog(local_catalog)
            emit_log(f"  ✅ Updated loader params: {list(loader_updates)}")

        for ctx_str, profile in results.items():
            if profile.get("error"):
                continue

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


async def _write_per_file_catalog_results(
    model_id: str,
    mode: str,
    results: dict[str, dict[str, Any]],
    emit_log: Callable[[str], None],
    loader_updates: dict[str, Any] | None,
) -> None:
    """
    Write measurement results to the per-file local catalog (~/.gateway/catalog/).

    Used for models added via the new per-file catalog system (not legacy inference_djinn).

    Reads the existing raw catalog entry, merges in measured device profiles and
    activated contexts, then writes back via CatalogManager.upsert_local_only.

    ∀ write: catalog_schema = 3 ∧ devices populated from results.
    """
    import copy

    from ..context_detection import determine_activated_contexts

    try:
        from ...core.catalog import get_catalog_loader
        from ...core.catalog_manager import get_catalog_manager

        entry = get_catalog_loader().get_model(model_id)
        if not entry:
            emit_log(f"  ⚠️ '{model_id}' not found in any catalog; cannot write results")
            return

        entry = copy.deepcopy(entry)

        # Merge measured profiles into devices section
        devices = entry.setdefault("devices", {})
        schema_name = entry.get("schema", "")
        success_count = 0

        for ctx_str, profile in results.items():
            if profile.get("error"):
                continue

            n_gpu_layers = profile.get("n_gpu_layers")
            if n_gpu_layers is None or n_gpu_layers == 0:
                device_key = "cpu"
            elif n_gpu_layers == -1:
                device_key = "gpu"
            else:
                device_key = "hybrid"

            profile_entry: dict[str, Any] = {
                "vram_mb": profile.get("vram_mb", 0),
                "ram_mb": profile.get("ram_mb", 0),
            }
            # Native GGUF engine persists n_gpu_layers per-profile for hybrid support
            if schema_name == "llama-cpp" and n_gpu_layers is not None:
                profile_entry["n_gpu_layers"] = n_gpu_layers

            devices.setdefault(device_key, {}).setdefault("profiles", {})[ctx_str] = (
                profile_entry
            )
            success_count += 1

        if not success_count:
            emit_log("  ⚠️ No successful profiles in results; nothing written")
            return

        # Persist loader routing params (e.g. gpu_memory_utilization for vLLM)
        if loader_updates:
            loader = entry.setdefault("loader", {})
            for key, value in loader_updates.items():
                loader.setdefault(key, value)

        # Activated contexts → metadata (stripped from static by _strip_measurement_data)
        gpu_contexts, cpu_contexts, reason = determine_activated_contexts(results, mode)
        if reason:
            emit_log(f"  → Activating: {reason}")
        metadata = entry.setdefault("metadata", {})
        if gpu_contexts:
            metadata["activated_gpu_contexts"] = gpu_contexts
        if cpu_contexts:
            metadata["activated_cpu_contexts"] = cpu_contexts

        _ = get_catalog_manager().upsert_local_only(model_id, entry)
        emit_log(
            f"  ✅ Per-file catalog updated: {success_count} profile(s), "
            f"devices={list(devices)}"
        )

    except Exception as e:
        emit_log(f"  ⚠️ Per-file catalog write failed: {e}")
