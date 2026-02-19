"""
Host-side VRAM measurement via pynvml.

Sums per-process GPU memory (same data source as nvtop).
Runs on the Master host where pynvml can see all GPU processes.
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_logging import get_logger

logger = get_logger(__name__)

ULLONG_SENTINEL = 1 << 50


@dataclass(slots=True, kw_only=True)
class VramSnapshot:
    """Per-process VRAM total for a single GPU device."""

    device_index: int
    total_mb: int
    process_count: int


def measure_gpu_vram(device_index: int = 0) -> VramSnapshot | None:
    """Sum per-process GPU memory on the host for a given device.

    Returns None if pynvml is unavailable or the device index is invalid.
    """
    try:
        import pynvml
    except ImportError:
        logger.warning("pynvml not installed — VRAM measurement unavailable")
        return None

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
    except pynvml.NVMLError as exc:
        logger.error(f"NVML init/device error for index {device_index}: {exc}")
        return None

    pid_to_mb: dict[int, int] = {}
    any_list_ok = False

    for list_fn in (
        pynvml.nvmlDeviceGetComputeRunningProcesses,
        pynvml.nvmlDeviceGetGraphicsRunningProcesses,
    ):
        try:
            procs = list_fn(handle)
            any_list_ok = True
        except pynvml.NVMLError:
            continue

        for p in procs:
            used = getattr(p, "usedGpuMemory", None)
            if used is None:
                continue
            used_i = int(used)
            if used_i > ULLONG_SENTINEL:
                continue
            mb = used_i // (1024 * 1024)
            pid_to_mb[p.pid] = max(pid_to_mb.get(p.pid, 0), mb)

    if not any_list_ok:
        logger.error("All NVML process list calls failed")
        return None

    return VramSnapshot(
        device_index=device_index,
        total_mb=sum(pid_to_mb.values()),
        process_count=len(pid_to_mb),
    )
