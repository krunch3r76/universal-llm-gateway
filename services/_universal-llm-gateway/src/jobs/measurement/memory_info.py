"""
System memory information and headroom computation for measurement probes.

Provides RAM/swap diagnostics and configurable headroom so measurement
subprocesses don't starve the host (sshd, etc.) under memory pressure.
"""

from typing import Any, Protocol, cast

from universal_logging import get_logger

from .common import env_float, env_int

logger = get_logger(__name__)

DEFAULT_RAM_HEADROOM_MIN_MB = 4096
DEFAULT_RAM_HEADROOM_MAX_MB = 16384
DEFAULT_RAM_HEADROOM_PCT = 0.10  # 10% of total RAM, capped by MAX_MB


class _VirtualMemoryLike(Protocol):
    total: int
    available: int


class _SwapMemoryLike(Protocol):
    total: int
    used: int


class _PsutilModuleLike(Protocol):
    def virtual_memory(self) -> _VirtualMemoryLike: ...
    def swap_memory(self) -> _SwapMemoryLike: ...


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

    base_headroom_mb = max(
        DEFAULT_RAM_HEADROOM_MIN_MB,
        min(DEFAULT_RAM_HEADROOM_MAX_MB, int(total_ram_mb * 0.10)),
    )

    # Insufficient swap increases OOM risk; compensate with more headroom.
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

    current_headroom_bytes = compute_ram_headroom_bytes(psutil_mod)
    current_headroom_mb = (
        int(current_headroom_bytes / (1024 * 1024)) if current_headroom_bytes else 0
    )

    safe_limit_mb = max(0, available_ram_mb - recommended_headroom_mb)

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
