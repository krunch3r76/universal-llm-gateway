"""Resource tracking package.

Provides comprehensive resource monitoring for model VRAM/RAM usage,
lifecycle states, and inference tracking.

Public API:
- ResourceTracker: Main tracking class
- ModelStatus: Model lifecycle states enum
- ModelResourceInfo: Per-model resource data
- SystemResourceInfo: System-wide resource snapshot
- resource_tracker: Global singleton instance
- Hardware queries: get_vram_info, get_ram_info, etc.

Note: ResourceTracker and resource_tracker use lazy import to avoid
circular import issues with workers module.
"""

from .hardware import (
    PSUTIL_AVAILABLE,
    PYNVML_AVAILABLE,
    get_process_gpu_memory,
    get_process_ram_usage,
    get_ram_info,
    get_vram_info,
    pid_exists,
)
from .queries import (
    detect_quantization,
    estimate_context_length,
    get_model_requirements,
    get_model_resources_from_config,
)
from .types import ModelResourceInfo, ModelStatus, SystemResourceInfo


def __getattr__(name: str):
    """Lazy import for ResourceTracker to avoid circular imports."""
    if name in ("ResourceTracker", "resource_tracker"):
        from .tracker import ResourceTracker, resource_tracker

        if name == "ResourceTracker":
            return ResourceTracker
        return resource_tracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ResourceTracker, resource_tracker lazy-loaded via __getattr__
# ruff: noqa: F822
__all__ = [
    # Core tracker (lazy import)
    "ResourceTracker",
    "resource_tracker",
    # Types
    "ModelStatus",
    "ModelResourceInfo",
    "SystemResourceInfo",
    # Hardware queries
    "get_vram_info",
    "get_ram_info",
    "get_process_gpu_memory",
    "get_process_ram_usage",
    "pid_exists",
    "PSUTIL_AVAILABLE",
    "PYNVML_AVAILABLE",
    # Config queries
    "get_model_requirements",
    "get_model_resources_from_config",
    "detect_quantization",
    "estimate_context_length",
]
