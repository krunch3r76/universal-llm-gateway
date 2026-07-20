"""System and per-inference resource telemetry event signals and factories.

Emitted by resource monitoring and tracker query paths so Stargate and local
subscribers can track VRAM/RAM without polling. Keeps discrete lifecycle
state on MODEL_LOADED/UNLOADED — this module carries metrics only.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from typing import Any

from universal_event_bus import Event, event_factory

# ========== System Resource Event Signals ==========

SYSTEM_RESOURCES_UPDATED = "system.resources.updated"
"""
Emitted when system resource information is updated.

Payload:
    total_vram_mb: int - Total VRAM available on the system in megabytes
    available_vram_mb: int - Currently available VRAM in megabytes
    total_ram_mb: int - Total RAM available on the system in megabytes
    available_ram_mb: int - Currently available RAM in megabytes
"""

INFERENCE_RESOURCE_UPDATE = "inference.resource.update"
"""
Emitted when resource usage is updated during inference.

Payload:
    model_id: str - Model identifier
    request_id: str - Request identifier
    timestamp: float - Timestamp of the reading
    vram_used_mb: int - Current VRAM usage in megabytes
    ram_used_mb: int - Current RAM usage in megabytes
    vram_max_mb: int - Maximum VRAM usage observed
    ram_max_mb: int - Maximum RAM usage observed
    gpu_utilization: float - GPU utilization percentage
    inference_duration: float - Duration of inference so far
    worker_config: Dict[str, Any] - Worker configuration associated with this reading
"""


# System Resource Event Factories
@event_factory
def SystemResourcesUpdated(
    total_vram_mb: int,
    available_vram_mb: int,
    total_ram_mb: int,
    available_ram_mb: int,
    model_vram: dict[str, int] | None = None,
) -> Event:
    """
    Create SYSTEM_RESOURCES_UPDATED event.

    Single-writer invariant: lifecycle state (loaded_models) is exclusively
    from MODEL_LOADED / MODEL_UNLOADED discrete events. RESOURCE_UPDATE
    carries only resource metrics and per-model VRAM measurements.

    Args:
        total_vram_mb: Total VRAM available in megabytes
        available_vram_mb: Currently available VRAM in megabytes
        total_ram_mb: Total RAM available in megabytes
        available_ram_mb: Currently available RAM in megabytes
        model_vram: Per-model actual VRAM consumption in MB (model_id → vram_mb).
            Populated from resource tracker measurements; used by Stargate eviction
            planner to replace stale load-time snapshots with current real values.

    Returns:
        Event with SystemResourcesUpdated signal
    """
    payload: dict[str, Any] = {
        "total_vram_mb": total_vram_mb,
        "available_vram_mb": available_vram_mb,
        "total_ram_mb": total_ram_mb,
        "available_ram_mb": available_ram_mb,
    }
    if model_vram is not None:
        payload["model_vram"] = model_vram
    return Event(
        signal=SYSTEM_RESOURCES_UPDATED,
        payload=payload,
    )


@event_factory
def InferenceResourceUpdate(
    model_id: str,
    request_id: str,
    timestamp: float,
    worker_config: dict[str, Any],
    vram_used_mb: int | None = None,
    ram_used_mb: int | None = None,
    vram_max_mb: int | None = None,
    ram_max_mb: int | None = None,
    gpu_utilization: float | None = None,
    inference_duration: float | None = None,
    peak_ram_gb: float | None = None,
    peak_vram_gb: float | None = None,
) -> Event:
    """
    Create INFERENCE_RESOURCE_UPDATE event.

    Args:
        model_id: Model identifier
        request_id: Request identifier
        timestamp: Timestamp of the reading
        worker_config: Worker configuration for this reading
        vram_used_mb: Current VRAM usage in megabytes (optional)
        ram_used_mb: Current RAM usage in megabytes (optional)
        vram_max_mb: Maximum VRAM usage observed (optional)
        ram_max_mb: Maximum RAM usage observed (optional)
        gpu_utilization: GPU utilization percentage (optional)
        inference_duration: Duration of inference so far (optional)
        peak_ram_gb: Peak RAM in GB (legacy, optional)
        peak_vram_gb: Peak VRAM in GB (legacy, optional)

    Returns:
        Event with InferenceResourceUpdate signal
    """
    payload = {
        "model_id": model_id,
        "request_id": request_id,
        "timestamp": timestamp,
        "worker_config": worker_config,
        "vram_used_mb": vram_used_mb,
        "ram_used_mb": ram_used_mb,
        "vram_max_mb": vram_max_mb,
        "ram_max_mb": ram_max_mb,
        "gpu_utilization": gpu_utilization,
        "inference_duration": inference_duration,
        "peak_ram_gb": peak_ram_gb,
        "peak_vram_gb": peak_vram_gb,
    }
    return Event(
        signal=INFERENCE_RESOURCE_UPDATE,
        payload={k: v for k, v in payload.items() if v is not None},
    )
