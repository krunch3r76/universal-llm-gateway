"""
Common utilities for measurement execution.

Provides system memory info, subprocess setup, and tracking
for both CPU and GPU measurement modes.
"""

import asyncio
import ctypes
import ctypes.util
import os
import signal
import sys
from typing import Any, Protocol, cast

from universal_logging import get_logger

logger = get_logger(__name__)

# Default headroom configuration
DEFAULT_RAM_HEADROOM_MIN_MB = 4096
DEFAULT_RAM_HEADROOM_MAX_MB = 16384
DEFAULT_RAM_HEADROOM_PCT = 0.10  # 10% of total RAM, capped by MAX_MB

# Measurement subprocess hardening
DEFAULT_SUBPROC_NICE = 19  # 0..19 (higher = lower priority)
DEFAULT_SUBPROC_OOM_SCORE_ADJ = 500  # 0..1000 (higher = more killable)


class _VirtualMemoryLike(Protocol):
    total: int
    available: int


class _SwapMemoryLike(Protocol):
    total: int
    used: int


class _PsutilModuleLike(Protocol):
    def virtual_memory(self) -> _VirtualMemoryLike: ...
    def swap_memory(self) -> _SwapMemoryLike: ...


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


def maybe_psutil() -> _PsutilModuleLike | None:
    """Optionally load psutil module for memory info."""
    try:
        import psutil  # type: ignore

        return cast(_PsutilModuleLike, cast(Any, psutil))
    except Exception:
        return None


def compute_ram_headroom_bytes(psutil_mod: _PsutilModuleLike | None) -> int | None:
    """
    Compute RAM headroom to keep the host responsive during measurement probes.

    Override with:
      - MEASUREMENT_RAM_HEADROOM_MB
      - MEASUREMENT_RAM_HEADROOM_PCT (default 0.10)
      - MEASUREMENT_RAM_HEADROOM_MIN_MB / _MAX_MB
    """
    override_mb = env_int("MEASUREMENT_RAM_HEADROOM_MB")
    if override_mb is not None and override_mb > 0:
        return override_mb * 1024 * 1024

    if not psutil_mod:
        return DEFAULT_RAM_HEADROOM_MIN_MB * 1024 * 1024

    total = psutil_mod.virtual_memory().total
    pct = env_float("MEASUREMENT_RAM_HEADROOM_PCT")
    if pct is None or pct <= 0:
        pct = DEFAULT_RAM_HEADROOM_PCT

    min_mb = env_int("MEASUREMENT_RAM_HEADROOM_MIN_MB") or DEFAULT_RAM_HEADROOM_MIN_MB
    max_mb = env_int("MEASUREMENT_RAM_HEADROOM_MAX_MB") or DEFAULT_RAM_HEADROOM_MAX_MB
    min_mb = max(0, min_mb)
    max_mb = max(min_mb, max_mb)

    computed_mb = int((total / (1024 * 1024)) * pct)
    headroom_mb = max(min_mb, min(max_mb, computed_mb))
    return headroom_mb * 1024 * 1024


def get_system_memory_info() -> dict[str, Any]:
    """
    Get system memory info with smart headroom recommendations.

    Returns dict with:
        - total_ram_mb, available_ram_mb
        - total_swap_mb, available_swap_mb
        - recommended_headroom_mb
        - current_headroom_mb
        - safe_measurement_limit_mb
        - warnings: list of warning messages
    """
    psutil_mod = maybe_psutil()
    warnings: list[str] = []

    if not psutil_mod:
        warnings.append("psutil not available; cannot determine system memory")
        return {
            "total_ram_mb": None,
            "available_ram_mb": None,
            "total_swap_mb": None,
            "available_swap_mb": None,
            "recommended_headroom_mb": DEFAULT_RAM_HEADROOM_MIN_MB,
            "current_headroom_mb": DEFAULT_RAM_HEADROOM_MIN_MB,
            "safe_measurement_limit_mb": None,
            "warnings": warnings,
        }

    vm = psutil_mod.virtual_memory()
    swap = psutil_mod.swap_memory()

    total_ram_mb = int(vm.total / (1024 * 1024))
    available_ram_mb = int(vm.available / (1024 * 1024))
    total_swap_mb = int(swap.total / (1024 * 1024))
    available_swap_mb = int((swap.total - swap.used) / (1024 * 1024))

    # Compute recommended headroom
    base_headroom_mb = max(
        DEFAULT_RAM_HEADROOM_MIN_MB,
        min(DEFAULT_RAM_HEADROOM_MAX_MB, int(total_ram_mb * 0.10)),
    )

    # If swap is less than 25% of RAM, increase headroom
    if total_swap_mb < (total_ram_mb * 0.25):
        swap_penalty_mb = int(total_ram_mb * 0.05)
        recommended_headroom_mb = min(
            DEFAULT_RAM_HEADROOM_MAX_MB, base_headroom_mb + swap_penalty_mb
        )
        warnings.append(
            f"Low/no swap detected ({total_swap_mb}MB); "
            f"increased headroom recommendation to {recommended_headroom_mb}MB"
        )
    else:
        recommended_headroom_mb = base_headroom_mb

    # Get current configured headroom
    current_headroom_bytes = compute_ram_headroom_bytes(psutil_mod)
    current_headroom_mb = (
        int(current_headroom_bytes / (1024 * 1024)) if current_headroom_bytes else 0
    )

    # Safe limit for measurement probes
    safe_limit_mb = max(0, available_ram_mb - recommended_headroom_mb)

    # Warnings
    if available_ram_mb < recommended_headroom_mb:
        warnings.append(
            f"CRITICAL: Available RAM ({available_ram_mb}MB) < "
            f"recommended headroom ({recommended_headroom_mb}MB)"
        )
        warnings.append("Measurement may cause system instability or freeze SSH")

    if available_ram_mb < recommended_headroom_mb * 2:
        warnings.append(
            f"WARNING: Low available RAM ({available_ram_mb}MB). "
            "Consider unloading other models or reducing context sizes."
        )

    if total_swap_mb == 0:
        warnings.append(
            "No swap configured; out-of-memory will cause immediate process kills"
        )
    elif total_swap_mb > 0 and available_swap_mb < 2048:
        warnings.append(
            f"Low available swap ({available_swap_mb}MB); avoid swap thrashing"
        )

    return {
        "total_ram_mb": total_ram_mb,
        "available_ram_mb": available_ram_mb,
        "total_swap_mb": total_swap_mb,
        "available_swap_mb": available_swap_mb,
        "recommended_headroom_mb": recommended_headroom_mb,
        "current_headroom_mb": current_headroom_mb,
        "safe_measurement_limit_mb": safe_limit_mb,
        "warnings": warnings,
    }


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
            f"SubprocessTracker.kill_all() called with {len(procs)} tracked processes"
        )
        for proc in procs:
            try:
                if proc.pid:
                    logger.info(f"Killing process group {proc.pid} with SIGKILL")
                    os.killpg(proc.pid, signal.SIGKILL)
                    logger.info(
                        f"Successfully sent SIGKILL to process group {proc.pid}"
                    )
            except Exception as e:
                logger.warning(f"Failed to kill process group {proc.pid}: {e}")
            try:
                _ = await asyncio.wait_for(proc.wait(), timeout=5)
                logger.info(f"Process {proc.pid} terminated")
            except TimeoutError:
                logger.warning(
                    f"Process {proc.pid} did not terminate within 5s timeout"
                )
            except Exception as e:
                logger.warning(f"Error waiting for process {proc.pid}: {e}")
