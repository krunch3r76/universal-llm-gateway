"""
CPU measurement execution.

Handles CPU-only measurements (n_gpu_layers=0) using run_cpu_test.
"""

import asyncio
from pathlib import Path
from typing import Any

from .runners import run_cpu_test


async def measure_cpu_context(
    model_path: Path,
    context: int,
    n_batch: int,
    mmproj_path: str | None,
) -> dict[str, Any]:
    """Measure single CPU context via executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        run_cpu_test,
        str(model_path),
        context,
        n_batch,
        mmproj_path,
    )
