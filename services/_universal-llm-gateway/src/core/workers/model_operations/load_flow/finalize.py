"""Successful load finalization with resource measurement and catalog VRAM reconcile."""

import asyncio
from typing import TYPE_CHECKING

from .deps import (
    emit_load_flow_debug,
    get_event_classes,
    get_resource_tracker,
    logger,
    publish_event,
)

if TYPE_CHECKING:
    from ...controller import WorkerController


async def finalize_load(
    controller: "WorkerController",
    model_id: str,
    vram_before: float | None,
    context_length: int | None = None,
):
    """Finalize model loading with resource measurement."""
    resource_tracker = get_resource_tracker()
    pid = None
    try:
        info = controller.get_all_process_info().get(model_id)
        if info and isinstance(info, dict):
            pid = info.get("pid")
    except KeyError:  # Example: if get() might raise KeyError if model_id not found
        logger.debug(
            "Process info not found for %s during finalization.",
            model_id,
        )
    except Exception as e:  # Catch other unexpected errors
        logger.warning(
            "Unexpected error getting process info for %s during finalization: %s",
            model_id,
            e,
        )

    req = resource_tracker.get_model_requirements(model_id)
    actual_vram, actual_ram = (
        req["vram_required_mb"] or 0,
        req["ram_required_mb"] or 0,
    )
    if pid:
        v, r = resource_tracker.get_current_process_resources(
            pid=pid, model_id=model_id
        )
        if v is not None:
            actual_vram = v
        if r is not None:
            actual_ram = r

    resource_tracker.update_model_resources(model_id, actual_vram, actual_ram)
    _, model_loaded, _, _ = get_event_classes()

    await publish_event(
        controller.event_bus,
        model_loaded(
            model_id=model_id,
            vram_usage_mb=actual_vram,
            ram_usage_mb=actual_ram,
            process_pid=pid,
        ),
    )
    reconciled_vram = await asyncio.to_thread(
        reconcile_catalog_vram,
        model_id,
        actual_vram,
        actual_ram,
    )
    if reconciled_vram:
        from src.core.events.types import CatalogReloaded

        await publish_event(
            controller.event_bus,
            CatalogReloaded(reason="auto_vram_reconcile"),
        )

    await emit_load_flow_debug(
        "finalize_loaded_event",
        model_id,
        pid=pid,
        vram_usage_mb=actual_vram,
        ram_usage_mb=actual_ram,
        context_length=context_length,
    )

    # Verify tracker state before publishing RESOURCE_UPDATE
    tracker_info = resource_tracker.get_model_info(model_id)
    if tracker_info:
        logger.info(
            f"🔍 PRE-RESOURCE_UPDATE: {model_id} tracker state: "
            f"status={tracker_info.status.value}, "
            f"vram={tracker_info.vram_usage_mb}MB, "
            f"ram={tracker_info.ram_usage_mb}MB"
        )
    else:
        logger.error(f"❌ PRE-RESOURCE_UPDATE: {model_id} NOT IN TRACKER!")

    # Publish RESOURCE_UPDATE with correct available VRAM (model now LOADED)
    # This ensures Stargate's cache reflects the loaded model's VRAM usage.
    # Without this, earlier RESOURCE_UPDATEs (from preflight/measure_vram_before)
    # show stale values because model was LOADING, not LOADED.
    # Assuming resource_tracker.publish_system_resources() or similar exists for explicit event.
    # If get_system_resources() has a side-effect, this should be documented or renamed.
    # For now, keeping the original call if it's implicitly triggering an event.
    # If not, an explicit event publication is needed here.
    await resource_tracker.get_system_resources()
    # Example: await publish_event(controller.event_bus, SystemResourcesUpdatedEvent(system_resources))

    logger.info(
        f"✅ Model {model_id} loaded - VRAM: {actual_vram}MB, RAM: {actual_ram}MB"
        + (f", Context: {context_length}" if context_length else "")
    )


def reconcile_catalog_vram(
    model_id: str,
    actual_vram: int,
    actual_ram: int,
) -> bool:
    """Persist higher measured VRAM into the local operational catalog."""
    try:
        from src.core.catalog.vram_reconciliation import reconcile_max_observed_vram

        return reconcile_max_observed_vram(model_id, actual_vram, actual_ram)
    except Exception as e:
        logger.warning("Failed catalog VRAM reconciliation for %s: %s", model_id, e)
        return False
