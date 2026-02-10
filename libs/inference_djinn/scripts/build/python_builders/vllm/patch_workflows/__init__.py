"""
vLLM Patch Workflow System.

Version-aware patching for vLLM builds that applies CMake patches
only for versions where they're verified to work.

Usage:
    from patch_workflows import VersionedPatcher, get_registry

    patcher = VersionedPatcher(
        source_dir=vllm_source,
        gpu_arch="120",
        vllm_version="0.13.2",
    )
    patcher.apply_patches()
"""

from .config import PatchDefinition, PatchWorkflow
from .patcher import VersionedPatcher
from .registry import PatchRegistry, get_registry

__all__ = [
    "PatchDefinition",
    "PatchWorkflow",
    "PatchRegistry",
    "VersionedPatcher",
    "get_registry",
]
