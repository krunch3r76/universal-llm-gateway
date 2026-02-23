"""
CPU measurement execution.

Handles CPU-only measurements (n_gpu_layers=0) via run_layer_test — the same
async subprocess path that GPU measurement uses.
"""

from pathlib import Path
from typing import Any

from .common import SubprocessTracker
from .gpu import run_layer_test


async def measure_cpu_context(
    model_path: Path,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    tracker: SubprocessTracker,
) -> dict[str, Any]:
    """Measure single CPU context via llama-server with n_gpu_layers=0."""
    profile = await run_layer_test(
        model_path,
        n_layers=0,
        context=context,
        n_batch=n_batch,
        gpu_index=gpu_index,
        mmproj_path=mmproj_path,
        tracker=tracker,
    )
    if profile.get("success"):
        profile["n_gpu_layers"] = 0
    return profile
