"""Check system resources and show measurement safety diagnostics."""

import argparse

from ..config import Config

# Inline the memory checking logic to avoid import issues
DEFAULT_RAM_HEADROOM_MIN_MB = 4096
DEFAULT_RAM_HEADROOM_MAX_MB = 16384
DEFAULT_RAM_HEADROOM_PCT = 0.10


def _get_system_memory_info() -> dict:
    """Get system memory info with smart headroom recommendations."""
    try:
        import psutil
    except ImportError:
        return {
            "total_ram_mb": None,
            "available_ram_mb": None,
            "total_swap_mb": None,
            "available_swap_mb": None,
            "recommended_headroom_mb": DEFAULT_RAM_HEADROOM_MIN_MB,
            "current_headroom_mb": DEFAULT_RAM_HEADROOM_MIN_MB,
            "safe_measurement_limit_mb": None,
            "warnings": ["psutil not available; cannot determine system memory"],
        }

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    total_ram_mb = int(vm.total / (1024 * 1024))
    available_ram_mb = int(vm.available / (1024 * 1024))
    total_swap_mb = int(swap.total / (1024 * 1024))
    available_swap_mb = int((swap.total - swap.used) / (1024 * 1024))

    warnings = []

    # Base headroom: 10% of RAM, capped between 4GB and 16GB
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

    current_headroom_mb = recommended_headroom_mb  # Use recommended as default
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


def cmd_check_resources(args: argparse.Namespace, config: Config) -> int:
    """
    Check system resources and show measurement safety recommendations.

    Shows:
    - Total and available RAM/swap
    - Current headroom configuration
    - Recommended headroom (to keep SSH/system responsive)
    - Safe memory limit per measurement probe
    - Warnings about low memory or missing swap
    """
    mem_info = _get_system_memory_info()

    # Display system memory info
    print("=== System Memory Resources ===")
    print()

    if mem_info.get("total_ram_mb") is None:
        print("❌ Unable to detect system memory (psutil not available)")
        return 1

    print(
        f"RAM:  {mem_info['available_ram_mb']:,}MB available / {mem_info['total_ram_mb']:,}MB total"
    )
    if mem_info.get("total_swap_mb"):
        print(
            f"Swap: {mem_info['available_swap_mb']:,}MB available / {mem_info['total_swap_mb']:,}MB total"
        )
    else:
        print("Swap: Not configured")

    print()
    print("=== Measurement Safety Configuration ===")
    print()
    print(
        f"Current headroom:     {mem_info['current_headroom_mb']:,}MB "
        f"(configured via env or default)"
    )
    print(
        f"Recommended headroom: {mem_info['recommended_headroom_mb']:,}MB "
        f"(to keep SSH/system responsive)"
    )

    if mem_info.get("safe_measurement_limit_mb", 0) > 0:
        print()
        print(
            f"Safe probe limit:     ~{mem_info['safe_measurement_limit_mb']:,}MB per measurement subprocess"
        )
        print("                      (available RAM minus recommended headroom)")

    # Show warnings
    if mem_info.get("warnings"):
        print()
        print("=== Warnings ===")
        print()
        for warning in mem_info["warnings"]:
            print(f"⚠️  {warning}")

    # Suggest environment variable configuration
    if args.suggest_env:
        print()
        print("=== Recommended Environment Variables ===")
        print()
        print("# Add to gateway service environment (systemd or start script):")
        print(
            f"export MEASUREMENT_RAM_HEADROOM_MB={mem_info['recommended_headroom_mb']}"
        )
        print()
        print("# Optional: hard cap subprocess address space (conservative)")
        safe_cap = max(
            8192,
            mem_info.get("safe_measurement_limit_mb", 0),
        )
        print(f"export MEASUREMENT_SUBPROC_AS_LIMIT_MB={safe_cap}")
        print()
        print("# Optional: adjust subprocess priority (0-19, higher = lower priority)")
        print("export MEASUREMENT_SUBPROC_NICE=19")
        print()
        print(
            "# Optional: adjust OOM kill preference (0-1000, higher = more likely to be killed)"
        )
        print("export MEASUREMENT_SUBPROC_OOM_SCORE_ADJ=500")

    # Recommendations
    print()
    print("=== Recommendations ===")
    print()

    if mem_info.get("available_ram_mb", 0) < mem_info.get("recommended_headroom_mb", 0):
        print("🛑 CRITICAL: Insufficient RAM for safe measurement")
        print("   - Unload all models before measuring")
        print("   - Reduce context sizes (use --contexts 16384,8192,4096)")
        print("   - Consider adding swap or upgrading RAM")
        return 1
    elif (
        mem_info.get("available_ram_mb", 0)
        < mem_info.get("recommended_headroom_mb", 0) * 1.5
    ):
        print("⚠️  WARNING: Low available RAM")
        print("   - Unload all models before measuring")
        print("   - Start with smaller contexts (--contexts 16384,8192,4096,2048)")
        print("   - Avoid measuring 131072 context (extremely large)")
        print("   - Use --disable-hybrid to skip layer binary search")
    else:
        print("✅ Sufficient RAM for measurement")
        print()
        print("Tips:")
        print("   - Unload all models before GPU measurement for accurate results")
        print("   - Start with default context detection (auto-detects from model)")
        print(
            "   - Very large contexts (>65536) may still cause issues with large models"
        )

    if mem_info.get("total_swap_mb", 0) == 0:
        print()
        print("💡 Consider adding swap space:")
        print("   - Provides safety buffer when RAM is exhausted")
        print("   - Prevents immediate OOM kills")
        print("   - Recommended: at least 8GB swap")

    return 0
