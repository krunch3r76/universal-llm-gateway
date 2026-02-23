"""
Common utilities for measurement execution.

Provides environment helpers, subprocess hardening, process-tree VRAM/RAM
measurement, and shared health-polling helpers used by both GGUF and vLLM probes.
"""

import asyncio
import ctypes
import ctypes.util
import os
import signal
import sys
from pathlib import Path

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)

# Measurement subprocess hardening
DEFAULT_SUBPROC_NICE = 19  # 0..19 (higher = lower priority)
DEFAULT_SUBPROC_OOM_SCORE_ADJ = 500  # 0..1000 (higher = more killable)


def env_int(name: str) -> int | None:
    """Parse integer from environment variable."""
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return None
    try:
        return int(v.strip())
    except Exception:
        return None


def env_float(name: str) -> float | None:
    """Parse float from environment variable."""
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return None
    try:
        return float(v.strip())
    except Exception:
        return None


def setup_measurement_subprocess(
    *,
    as_limit_bytes: int | None,
    nice_value: int,
    oom_score_adj: int | None,
) -> None:
    """
    Subprocess hardening to avoid host-wide stalls under OOM pressure.

    - Lower CPU priority (nice)
    - Increase OOM kill preference (oom_score_adj)
    - Ensure clean killability (pdeathsig + new process group)
    """
    _setup_death_signal()

    # Be "polite" on CPU so the host (and sshd) stays responsive.
    try:
        nice_value = max(0, min(19, nice_value))
        if nice_value:
            _ = os.nice(nice_value)
    except Exception as e:
        logger.warning(f"Failed to set nice for measurement subprocess: {e}")

    # Prefer killing measurement probes over system services under OOM.
    if oom_score_adj is not None and sys.platform.startswith("linux"):
        try:
            adj = max(0, min(1000, int(oom_score_adj)))
            with open("/proc/self/oom_score_adj", "w") as f:
                _ = f.write(str(adj))
        except Exception as e:
            logger.warning(f"Failed to set oom_score_adj for subprocess: {e}")

    # RLIMIT_AS intentionally not applied (incompatible with mmap-based loading)
    _ = as_limit_bytes


def _setup_death_signal() -> None:
    """
    Configure subprocess to receive SIGKILL when parent dies.

    Uses prctl(PR_SET_PDEATHSIG, SIGKILL) on Linux.
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        PR_SET_PDEATHSIG = 1  # noqa: N806 - Linux constant
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception as e:
        logger.warning(f"Failed to set death signal for subprocess: {e}")

    # Create new process group for clean killability
    try:
        os.setpgrp()
    except Exception as e:
        logger.warning(f"Failed to create new process group: {e}")


def _get_descendant_pids(pid: int) -> list[int]:
    """Return list of descendant PIDs (children, grandchildren, etc.) via /proc."""
    descendants: list[int] = []
    try:
        import psutil

        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            descendants.append(child.pid)
        return descendants
    except Exception:
        pass
    # /proc fallback: walk /proc/*/stat for ppid chains
    try:
        children_map: dict[int, list[int]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_line = (entry / "stat").read_text()
                parts = stat_line.split(")")
                if len(parts) < 2:
                    continue
                fields = parts[-1].split()
                ppid = int(fields[1])  # field index 1 after closing paren
                child_pid = int(entry.name)
                children_map.setdefault(ppid, []).append(child_pid)
            except Exception:
                continue
        stack = children_map.get(pid, [])
        while stack:
            child = stack.pop()
            descendants.append(child)
            stack.extend(children_map.get(child, []))
    except Exception:
        pass
    return descendants


def measure_process_vram_mb(pid: int, device_index: int = 0) -> int | None:
    """Per-process VRAM via pynvml.nvmlDeviceGetComputeRunningProcesses().

    Sums VRAM for pid and all descendants (vLLM V1 forks EngineCore).
    Returns None if pynvml unavailable or process not found on GPU.
    """
    try:
        import pynvml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pynvml not available; cannot measure per-process VRAM")
        return None

    target_pids = {pid, *_get_descendant_pids(pid)}
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
        processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
        total_bytes = sum(p.usedGpuMemory for p in processes if p.pid in target_pids)
        pynvml.nvmlShutdown()
        if total_bytes == 0:
            return None
        return int(total_bytes // (1024 * 1024))
    except Exception as e:
        logger.warning("pynvml measurement failed: %s", e)
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
        return None


def get_total_vram_bytes(device_index: int = 0) -> int | None:
    """Query total GPU VRAM in bytes via pynvml.

    Returns None if pynvml is unavailable or query fails.
    """
    try:
        import pynvml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pynvml not available; cannot query total VRAM")
        return None
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total = mem_info.total
        pynvml.nvmlShutdown()
        return total
    except Exception as e:
        logger.warning("pynvml total VRAM query failed: %s", e)
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
        return None


def measure_process_tree_rss_mb(pid: int) -> int:
    """Sum RSS of process + descendants via /proc/{pid}/status.

    Falls back to psutil Process.children(recursive=True) if available.
    """
    target_pids = [pid, *_get_descendant_pids(pid)]
    total_rss_kb = 0
    for target in target_pids:
        try:
            with open(f"/proc/{target}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total_rss_kb += int(line.split()[1])
                        break
        except Exception:
            continue
    if total_rss_kb > 0:
        return total_rss_kb // 1024
    # psutil fallback
    try:
        import psutil  # type: ignore[import-untyped]

        proc = psutil.Process(pid)
        total = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except Exception:
                continue
        return int(total // (1024 * 1024))
    except Exception as e:
        logger.warning("Failed to measure RSS for pid %d: %s", pid, e)
        return 0


# ---------------------------------------------------------------------------
# Shared subprocess health-polling helpers (used by both GGUF and vLLM probes)
# ---------------------------------------------------------------------------


async def _capture_process_error(
    proc: asyncio.subprocess.Process,
    *,
    process_name: str,
) -> str:
    """Read stderr from a dead process and format error message."""
    stderr_text = ""
    if proc.stderr:
        try:
            raw = await proc.stderr.read()
            stderr_text = raw.decode(errors="replace").strip()
        except Exception:
            pass
    exit_code = proc.returncode if proc.returncode is not None else "unknown"
    msg = f"{process_name} died with exit code {exit_code}"
    if stderr_text:
        # Truncate to last 2000 chars to avoid huge error messages
        if len(stderr_text) > 2000:
            stderr_text = "..." + stderr_text[-2000:]
        msg += f"\nstderr: {stderr_text}"
    return msg


async def poll_health(
    socket_path: str,
    proc: asyncio.subprocess.Process,
    timeout_sec: int,
    *,
    poll_interval: float,
    process_name: str,
) -> str | None:
    """Poll GET /health until 200. Returns error string or None on success."""
    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost", timeout=10.0
    ) as client:
        elapsed = 0.0
        while elapsed < timeout_sec:
            if proc.returncode is not None:
                return await _capture_process_error(proc, process_name=process_name)
            try:
                os.kill(proc.pid, 0)
            except OSError:
                return await _capture_process_error(proc, process_name=process_name)

            try:
                response = await client.get("/health")
                if response.status_code == 200:
                    return None
            except (httpx.RequestError, httpx.HTTPStatusError):
                pass

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    return f"timeout after {timeout_sec}s waiting for /health"


async def kill_measurement_process(
    proc: asyncio.subprocess.Process,
    *,
    sigterm_timeout: float,
) -> None:
    """SIGTERM → wait sigterm_timeout → SIGKILL."""
    try:
        if proc.pid:
            os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=sigterm_timeout)
    except TimeoutError:
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass


class SubprocessTracker:
    """
    Track measurement subprocesses for cancellation on job cancel.

    Subprocesses are configured with PR_SET_PDEATHSIG so kernel automatically
    kills them if parent dies. This tracker only handles explicit cancellation.
    """

    def __init__(self) -> None:
        self._procs: set[asyncio.subprocess.Process] = set()

    def add(self, proc: asyncio.subprocess.Process) -> None:
        self._procs.add(proc)

    def discard(self, proc: asyncio.subprocess.Process) -> None:
        self._procs.discard(proc)

    async def kill_all(self) -> None:
        """Kill all tracked processes (explicit cancellation only)."""
        procs = list(self._procs)
        self._procs.clear()
        logger.info(
            "SubprocessTracker.kill_all() called with %d tracked processes", len(procs)
        )
        for proc in procs:
            try:
                if proc.pid:
                    logger.info("Killing process group %s with SIGKILL", proc.pid)
                    os.killpg(proc.pid, signal.SIGKILL)
            except Exception as e:
                logger.warning("Failed to kill process group %s: %s", proc.pid, e)
            try:
                _ = await asyncio.wait_for(proc.wait(), timeout=5)
                logger.info("Process %s terminated", proc.pid)
            except TimeoutError:
                logger.warning(
                    "Process %s did not terminate within 5s timeout", proc.pid
                )
            except Exception as e:
                logger.warning("Error waiting for process %s: %s", proc.pid, e)
