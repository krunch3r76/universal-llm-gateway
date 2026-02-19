"""Hardware resource queries via pynvml and psutil.

Provides real-time hardware resource measurement:
- GPU memory via pynvml (NVIDIA GPUs)
- System RAM via psutil
- Per-process resource tracking

All functions handle missing libraries gracefully with None returns.
"""

from universal_logging import get_logger

logger = get_logger(__name__)

# Optional dependencies - graceful degradation
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available - RAM tracking limited")

try:
    import pynvml

    pynvml.nvmlInit()
    PYNVML_AVAILABLE = True
except ImportError:
    pynvml = None
    PYNVML_AVAILABLE = False
    logger.warning("pynvml not available - GPU tracking limited")


def get_vram_info() -> dict[str, int]:
    """
    Get real-time VRAM information from GPU hardware.

    Queries all NVIDIA GPUs and aggregates total/free memory.

    Returns:
        Dict with 'total_vram_mb' and 'available_vram_mb'.
        Returns zeros if pynvml unavailable or query fails.
    """
    if not PYNVML_AVAILABLE:
        return {"total_vram_mb": 0, "available_vram_mb": 0}

    try:
        device_count = pynvml.nvmlDeviceGetCount()
        total_vram_mb = 0
        available_vram_mb = 0

        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_vram_mb += int(mem_info.total / (1024 * 1024))
            available_vram_mb += int(mem_info.free / (1024 * 1024))

        return {
            "total_vram_mb": total_vram_mb,
            "available_vram_mb": available_vram_mb,
        }
    except Exception as e:
        logger.warning(f"Failed to get VRAM info: {e}")
        return {"total_vram_mb": 0, "available_vram_mb": 0}


def get_ram_info() -> dict[str, int]:
    """
    Get real-time RAM information from system.

    Returns:
        Dict with 'total_ram_mb' and 'available_ram_mb'.
        Returns zeros if psutil unavailable or query fails.
    """
    if not PSUTIL_AVAILABLE:
        return {"total_ram_mb": 0, "available_ram_mb": 0}

    try:
        virtual_memory = psutil.virtual_memory()
        return {
            "total_ram_mb": int(virtual_memory.total / (1024 * 1024)),
            "available_ram_mb": int(virtual_memory.available / (1024 * 1024)),
        }
    except Exception as e:
        logger.warning(f"Failed to get RAM info: {e}")
        return {"total_ram_mb": 0, "available_ram_mb": 0}


def _collect_process_tree_pids(pid: int) -> set[int]:
    """Collect PIDs for a process and all its descendants.

    Engines like vLLM spawn child processes (e.g. VLLM::EngineCore) that hold
    the actual model weights.  Summing only the parent would dramatically
    under-report VRAM.
    """
    pids = {pid}
    if not PSUTIL_AVAILABLE:
        return pids
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            pids.add(child.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return pids


def get_process_gpu_memory(pid: int) -> int | None:
    """Get GPU memory for a process *tree* (parent + all descendants).

    Multiprocess engines (vLLM) split work across processes: the worker
    holds a small CUDA context while a child EngineCore holds the model
    weights.  We sum VRAM across the full tree so callers get the true
    footprint.

    Args:
        pid: Root process ID of the worker.

    Returns:
        Total GPU memory in MB across the process tree, or None if
        measurement failed or no GPU memory found.
    """
    if not PYNVML_AVAILABLE or not pid:
        return None

    try:
        tree_pids = _collect_process_tree_pids(pid)
        total_vram_mb = 0
        matched = False

        for device_id in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(device_id)
            procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            for proc in procs:
                if proc.pid in tree_pids:
                    mb = int(proc.usedGpuMemory / (1024 * 1024))
                    total_vram_mb += mb
                    matched = True

        if matched:
            if len(tree_pids) > 1:
                logger.debug(
                    f"Process tree {pid} ({len(tree_pids)} pids) "
                    f"using {total_vram_mb}MB VRAM"
                )
            else:
                logger.debug(f"Process {pid} using {total_vram_mb}MB VRAM")
            return total_vram_mb

        return None
    except Exception as e:
        logger.warning(f"Failed to get GPU memory for PID {pid}: {e}")
        return None


def get_process_ram_usage(pid: int) -> int | None:
    """Get RSS for a process *tree* (parent + all descendants).

    Args:
        pid: Root process ID of the worker.

    Returns:
        Total RAM usage in MB (RSS) across the process tree, or None if
        measurement failed.
    """
    if not PSUTIL_AVAILABLE or not pid:
        return None

    try:
        tree_pids = _collect_process_tree_pids(pid)
        total_ram_mb = 0
        for p in tree_pids:
            try:
                total_ram_mb += int(psutil.Process(p).memory_info().rss / (1024 * 1024))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        if len(tree_pids) > 1:
            logger.debug(
                f"Process tree {pid} ({len(tree_pids)} pids) using {total_ram_mb}MB RAM"
            )
        else:
            logger.debug(f"Process {pid} using {total_ram_mb}MB RAM")
        return total_ram_mb
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception as e:
        logger.warning(f"Failed to get RAM usage for PID {pid}: {e}")
        return None


def pid_exists(pid: int) -> bool:
    """Check if a process exists."""
    if not PSUTIL_AVAILABLE or not pid:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False
