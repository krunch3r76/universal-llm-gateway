"""
Patch workflow for vLLM v0.14.x.

These patches are EXPERIMENTAL and NOT applied by default.
v0.14.0 changed CMake structure significantly; patches need verification.

Use --apply-patches to force application for testing.

NOTE: Testing reveals v0.14.0 respects VLLM_FLASH_ATTN_FA2_ARCHS environment
variable without needing CMakeLists.txt patches. Single-arch builds work via
env vars alone. Patches kept for completeness but may not be necessary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import PatchDefinition, PatchWorkflow

if TYPE_CHECKING:
    from ..registry import PatchRegistry

# NOTE: v0.14.0 uses different CMake patterns than v0.13.x
# The patches below are placeholders that need verification.
# When tested and confirmed working, set verified_working=True.

# Flash Attention FA2 patch - NEEDS VERIFICATION for v0.14
# The CMake structure may have changed
FA2_PATCH = PatchDefinition(
    file_path="csrc/flash_attn/CMakeLists.txt",
    old_pattern='        cuda_archs_loose_intersection(FA2_ARCHS "8.0+PTX" "${CUDA_ARCHS}")',
    new_template="""        if(DEFINED ENV{{VLLM_FLASH_ATTN_FA2_ARCHS}})
            set(FA2_ARCHS "$ENV{{VLLM_FLASH_ATTN_FA2_ARCHS}}")
        else()
            cuda_archs_loose_intersection(FA2_ARCHS "8.0+PTX" "${{CUDA_ARCHS}}")
        endif()""",
    description="FA2 targeting (EXPERIMENTAL for v0.14)",
    optional=True,  # Pattern may not exist in v0.14
)

# Marlin patch - NEEDS VERIFICATION for v0.14
MARLIN_PATCH = PatchDefinition(
    file_path="CMakeLists.txt",
    old_pattern='cuda_archs_loose_intersection(MARLIN_ARCHS "8.0;8.7;9.0+PTX"',
    new_template='cuda_archs_loose_intersection(MARLIN_ARCHS "8.0;8.7;9.0;{arch_dotted}"',
    description="Marlin kernels (EXPERIMENTAL for v0.14)",
    optional=True,
)

# The v0.14 workflow
WORKFLOW = PatchWorkflow(
    name="v0.14",
    version_pattern=r"^0\.14\.\d+",
    patches=[
        FA2_PATCH,
        MARLIN_PATCH,
    ],
    verified_working=False,  # NOT verified - patches may not work
    notes=(
        "v0.14.0 changed CMake structure significantly. "
        "Patches are experimental and may not produce single-arch builds. "
        "Use --apply-patches to test, and report results."
    ),
)


def register(registry: PatchRegistry) -> None:
    """Register v0.14 workflow with the registry."""
    registry.register(WORKFLOW)
