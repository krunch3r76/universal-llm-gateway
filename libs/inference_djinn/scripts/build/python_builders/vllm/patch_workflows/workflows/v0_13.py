"""
Patch workflow for vLLM v0.13.x.

These patches are VERIFIED WORKING for v0.13.x and applied by default.
They modify CMakeLists.txt files to force exclusive architecture targeting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import PatchDefinition, PatchWorkflow

if TYPE_CHECKING:
    from ..registry import PatchRegistry

# Flash Attention FA2 patch - use environment variable for architecture
FA2_PATCH = PatchDefinition(
    file_path="csrc/flash_attn/CMakeLists.txt",
    old_pattern='        cuda_archs_loose_intersection(FA2_ARCHS "8.0+PTX" "${CUDA_ARCHS}")',
    new_template="""        if(DEFINED ENV{{VLLM_FLASH_ATTN_FA2_ARCHS}})
            set(FA2_ARCHS "$ENV{{VLLM_FLASH_ATTN_FA2_ARCHS}}")
        else()
            cuda_archs_loose_intersection(FA2_ARCHS "8.0+PTX" "${{CUDA_ARCHS}}")
        endif()""",
    description="FA2 will target SM_{gpu_arch} exclusively",
)

# Flash Attention FA3 patch - use environment variable for architecture
FA3_PATCH = PatchDefinition(
    file_path="csrc/flash_attn/CMakeLists.txt",
    old_pattern='        cuda_archs_loose_intersection(FA3_ARCHS "8.0;9.0a;" "${CUDA_ARCHS}")',
    new_template="""        if(DEFINED ENV{{VLLM_FLASH_ATTN_FA3_ARCHS}})
            set(FA3_ARCHS "$ENV{{VLLM_FLASH_ATTN_FA3_ARCHS}}")
        else()
            cuda_archs_loose_intersection(FA3_ARCHS "8.0;9.0a;" "${{CUDA_ARCHS}}")
        endif()""",
    description="FA3 disabled for optimized build (compatibility)",
    optional=True,  # FA3 may not exist in all versions
)

# Marlin regular kernels patch - add target architecture
MARLIN_REGULAR_PATCH = PatchDefinition(
    file_path="CMakeLists.txt",
    old_pattern='cuda_archs_loose_intersection(MARLIN_ARCHS "8.0;8.7;9.0+PTX"',
    new_template='cuda_archs_loose_intersection(MARLIN_ARCHS "8.0;8.7;9.0;{arch_dotted}"',
    description="Marlin regular kernels: SM_{gpu_arch} support added",
)

# Marlin MOE kernels patch - add target architecture
MARLIN_MOE_PATCH = PatchDefinition(
    file_path="CMakeLists.txt",
    old_pattern='cuda_archs_loose_intersection(MARLIN_MOE_ARCHS "8.0;8.7;9.0+PTX"',
    new_template='cuda_archs_loose_intersection(MARLIN_MOE_ARCHS "8.0;8.7;9.0;{arch_dotted}"',
    description="Marlin MOE kernels: SM_{gpu_arch} support added",
    optional=True,
)

# CUTLASS MOE Data v13.0+ patch - exclusive architecture
CUTLASS_V13_PATCH = PatchDefinition(
    file_path="CMakeLists.txt",
    old_pattern='cuda_archs_loose_intersection(CUTLASS_MOE_DATA_ARCHS "9.0a;10.0a;10.1a;10.3a;12.0a;12.1a;12.2a"',
    new_template='set(CUTLASS_MOE_DATA_ARCHS "{arch_dotted}"',
    description="CUTLASS MOE Data v13.0+: SM_{gpu_arch} EXCLUSIVE",
    optional=True,
)

# CUTLASS MOE Data v12.3+ patch - exclusive architecture
CUTLASS_V12_PATCH = PatchDefinition(
    file_path="CMakeLists.txt",
    old_pattern='cuda_archs_loose_intersection(CUTLASS_MOE_DATA_ARCHS "9.0a;10.0a;10.1a;10.3a;12.0a;12.1a"',
    new_template='set(CUTLASS_MOE_DATA_ARCHS "{arch_dotted}"',
    description="CUTLASS MOE Data v12.3+: SM_{gpu_arch} EXCLUSIVE",
    optional=True,
)

# The v0.13 workflow
WORKFLOW = PatchWorkflow(
    name="v0.13",
    version_pattern=r"^0\.13\.\d+",
    patches=[
        FA2_PATCH,
        FA3_PATCH,
        MARLIN_REGULAR_PATCH,
        MARLIN_MOE_PATCH,
        CUTLASS_V13_PATCH,
        CUTLASS_V12_PATCH,
    ],
    verified_working=True,
    notes="Verified working for vLLM v0.13.x releases",
)


def register(registry: PatchRegistry) -> None:
    """Register v0.13 workflow with the registry."""
    registry.register(WORKFLOW)
