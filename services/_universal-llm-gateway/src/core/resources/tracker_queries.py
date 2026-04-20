"""Runtime query functions for ResourceTracker.

Read-only accessors that query the tracker's internal state.
Thread Safety: Not needed. All calls from async event loop.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .hardware import get_ram_info, get_vram_info
from .queries import get_model_requirements, get_model_resources_from_config
from .types import ModelResourceInfo, ModelStatus, SystemResourceInfo

if TYPE_CHECKING:
    from model_id import ModelId

    from .tracker import ResourceTracker

logger = get_logger(__name__)


def _tracking_key(model_id: str | ModelId) -> str:
    """Per-variant key for state machines and ModelResourceInfo."""
    from src.core.resources.tracker import _tracking_key

    return _tracking_key(model_id)


def get_model_info(
    tracker: ResourceTracker, model_id: str | ModelId
) -> ModelResourceInfo | None:
    """Get resource information for a specific model variant.

    Uses tracking_key (preserves -hybrid) for per-variant lookup.
    """
    return tracker._models.get(_tracking_key(model_id))


def get_all_models_info(tracker: ResourceTracker) -> dict[str, ModelResourceInfo]:
    """Get resource information for all tracked models."""
    return dict(tracker._models)


def get_loaded_models(tracker: ResourceTracker) -> list[str]:
    """Get list of currently loaded models."""
    return [
        model_id
        for model_id, info in tracker._models.items()
        if info.status in [ModelStatus.LOADED, ModelStatus.BUSY]
    ]


def get_busy_models(tracker: ResourceTracker) -> list[str]:
    """Get list of models unavailable for new requests."""
    return [
        model_id
        for model_id, info in tracker._models.items()
        if info.status in [ModelStatus.BUSY, ModelStatus.LOADING, ModelStatus.UNLOADING]
    ]


def get_operations_in_progress(tracker: ResourceTracker) -> dict[str, list[str]]:
    """Get models currently being loaded or unloaded."""
    loading = [
        model_id
        for model_id, info in tracker._models.items()
        if info.status == ModelStatus.LOADING
    ]
    unloading = [
        model_id
        for model_id, info in tracker._models.items()
        if info.status == ModelStatus.UNLOADING
    ]
    return {"loading": loading, "unloading": unloading}


def get_state_machine_status(
    tracker: ResourceTracker, model_id: str | ModelId
) -> dict[str, Any] | None:
    """Get state machine status for a model variant."""
    key = _tracking_key(model_id)

    if key in tracker._state_machines:
        return tracker._state_machines[key].get_status()

    from model_id import ModelId as RuntimeModelId

    model = RuntimeModelId.parse(model_id) if isinstance(model_id, str) else model_id
    for tracked_key, sm in tracker._state_machines.items():
        tracked_model = RuntimeModelId.parse(tracked_key)
        if tracked_model.matches(model):
            return sm.get_status()

    return None


async def get_system_resources(tracker: ResourceTracker) -> SystemResourceInfo:
    """Get current system resource information with conservative estimates.

    Uses max(catalog_estimated_usage, actual_hardware_usage) to ensure
    we never over-estimate available resources.

    Invariant: available = total - max(catalog_estimate, hardware_used)
    """
    if not tracker._initialized:
        tracker._initialize_system_resources()

    if not tracker._system_info:
        return SystemResourceInfo()

    vram_info = get_vram_info()
    ram_info = get_ram_info()

    total_vram = vram_info["total_vram_mb"]
    total_ram = ram_info["total_ram_mb"]
    hardware_available_vram = vram_info["available_vram_mb"]
    hardware_available_ram = ram_info["available_ram_mb"]

    # Calculate hardware-measured used resources
    hardware_used_vram = total_vram - hardware_available_vram
    hardware_used_ram = total_ram - hardware_available_ram

    # Calculate catalog-estimated used resources from loaded models
    catalog_used_vram = 0
    catalog_used_ram = 0
    loaded_models_list = []
    model_vram: dict[str, int] = {}  # Per-model actual VRAM for eviction planning
    for model_id, info in tracker._models.items():
        if info.status in [ModelStatus.LOADED, ModelStatus.BUSY]:
            catalog_used_vram += info.vram_usage_mb
            catalog_used_ram += info.ram_usage_mb
            loaded_models_list.append(
                f"{model_id}(status={info.status.value}, vram={info.vram_usage_mb}MB)"
            )
            if info.vram_usage_mb > 0:
                model_vram[model_id] = info.vram_usage_mb

    # Use conservative estimate: max of catalog vs hardware
    conservative_used_vram = max(catalog_used_vram, hardware_used_vram)
    conservative_used_ram = max(catalog_used_ram, hardware_used_ram)

    # Available = total - conservative used
    available_vram = max(0, total_vram - conservative_used_vram)
    available_ram = max(0, total_ram - conservative_used_ram)

    # Debug logging for VRAM calculation tracing
    logger.info(
        f"🔍 get_system_resources: "
        f"total_vram={total_vram}MB, hardware_used={hardware_used_vram}MB, "
        f"catalog_used={catalog_used_vram}MB (from {len(loaded_models_list)} models), "
        f"conservative_used={conservative_used_vram}MB, "
        f"will_report_available={available_vram}MB | "
        f"Loaded models: {loaded_models_list or 'NONE'}"
    )

    system_info = SystemResourceInfo(
        total_vram_mb=total_vram,
        available_vram_mb=available_vram,
        total_ram_mb=total_ram,
        available_ram_mb=available_ram,
        timestamp=time.time(),
    )

    if tracker.event_bus:
        try:
            from ..events.types import SystemResourcesUpdated

            logger.info(
                f"📤 Publishing SYSTEM_RESOURCES_UPDATED event: "
                f"available_vram={available_vram}MB, available_ram={available_ram}MB, "
                f"loaded_models={loaded_models_list or 'NONE'}"
            )
            await tracker.event_bus.publish_nowait(
                SystemResourcesUpdated(
                    total_vram_mb=system_info.total_vram_mb,
                    available_vram_mb=system_info.available_vram_mb,
                    total_ram_mb=system_info.total_ram_mb,
                    available_ram_mb=system_info.available_ram_mb,
                    model_vram=model_vram or None,
                )
            )
        except Exception as e:
            logger.warning(f"⚠️ Failed to publish resources event: {e}")

    return system_info


def get_model_requirements_wrapper(model_id: str | ModelId) -> dict[str, Any]:
    """Get resource requirements for a model."""
    from model_id import ModelId

    if isinstance(model_id, ModelId):
        model_id = str(model_id)
    return get_model_requirements(model_id)


def get_model_resources_from_config_wrapper(
    model_id: str | ModelId,
) -> tuple[int | None, int | None]:
    """Get resource requirements from YAML configuration."""
    from model_id import ModelId

    if isinstance(model_id, ModelId):
        model_id = str(model_id)
    return get_model_resources_from_config(model_id)
