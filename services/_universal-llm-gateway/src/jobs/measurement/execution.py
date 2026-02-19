"""
Measurement execution logic for VRAM/RAM profiling.

Provides unified context measurement for CPU and GPU modes with
timing validation. Acts as thin orchestration over common.py, cpu.py, gpu.py.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .common import SubprocessTracker

# Re-export for backward compatibility (callers import from execution.py)
from .common import (
    get_system_memory_info as get_system_memory_info,  # noqa: F401, PLC0414
)
from .cpu import measure_cpu_context
from .gguf_reader import extract_block_count
from .gpu import (
    measure_gpu_context,
    measure_hybrid_context,
)
from .timing import (
    TimingTracker,
    validate_and_log_timing,
)

logger = get_logger(__name__)


async def measure_gpu_with_stepdown(
    model_path: Path,
    contexts: list[int],
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    enable_hybrid: bool,
    emit_log: Callable[[str], None],
    tracker: SubprocessTracker,
    safety_margin: int | None = None,
    validate_timing: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Measure GPU profiles, stepping down until a context fits.

    Args:
        model_path: Path to model file
        contexts: List of context lengths to measure (descending order)
        n_batch: Batch size
        gpu_index: GPU device index
        mmproj_path: Optional multimodal projector path
        enable_hybrid: Enable hybrid fallback when full GPU fails
        emit_log: Logging callback
        tracker: SubprocessTracker for GPU processes
        safety_margin: Safety margin for hybrid measurements
        validate_timing: Enable timing monotonicity validation

    Returns profiles for:
    1. The largest context that fits entirely on GPU (n_gpu_layers=-1)
    2. Training context (if different, may use partial offload)
    3. Hybrid profiles when enable_hybrid=True and full GPU fails
    """
    results: dict[str, dict[str, Any]] = {}
    found_full_gpu = False
    prev_layers_hint = None
    timing_tracker = TimingTracker() if validate_timing else None

    total_layers = extract_block_count(model_path)
    if total_layers:
        emit_log(f"Model has {total_layers} layers (from GGUF metadata)")
    else:
        emit_log(
            "⚠ Could not extract layer count from metadata — "
            "using n_gpu_layers=-1 (hidden hybrid detection disabled)"
        )

    for ctx in contexts:
        emit_log(f"Measuring GPU context {ctx}...")
        if total_layers:
            emit_log(
                f"  → Loading model with full GPU offload ({total_layers} layers)..."
            )
        else:
            emit_log("  → Loading model with full GPU offload (n_gpu_layers=-1)...")

        try:
            profile = await measure_gpu_context(
                model_path,
                ctx,
                n_batch,
                gpu_index,
                mmproj_path,
                tracker,
                total_layers,
            )
            if profile.get("success"):
                # Validate timing (shared logic with CPU)
                profile = validate_and_log_timing(
                    profile, ctx, timing_tracker, emit_log
                )

                # Check for timing anomaly - stop measuring if detected
                if profile.get("error"):
                    emit_log(f"  → Stopping - keeping {len(results)} valid contexts")
                    break

                results[str(ctx)] = profile
                log_profile_result(ctx, profile, emit_log)
                if not found_full_gpu:
                    found_full_gpu = True
                    emit_log(f"  → Max full-GPU context: {ctx}")
            else:
                # Log stderr if available for debugging
                if stderr := profile.get("stderr"):
                    emit_log(f"  → Error details: {stderr}")
                # Full GPU failed - try hybrid if enabled
                if enable_hybrid:
                    hybrid = await try_hybrid_measurement(
                        model_path,
                        ctx,
                        n_batch,
                        gpu_index,
                        mmproj_path,
                        prev_layers_hint,
                        emit_log,
                        tracker,
                        safety_margin,
                    )
                    if hybrid:
                        # Validate timing for hybrid (shared logic)
                        hybrid = validate_and_log_timing(
                            hybrid, ctx, timing_tracker, emit_log, "in hybrid"
                        )

                        # Check for timing anomaly in hybrid - stop if detected
                        if hybrid.get("error"):
                            emit_log(
                                f"  → Stopping - keeping {len(results)} valid contexts"
                            )
                            break

                        results[str(ctx)] = hybrid
                        prev_layers_hint = hybrid.get("n_gpu_layers")
                    else:
                        emit_log(f"  ❌ {ctx}: Hybrid also failed, stepping down...")
                else:
                    emit_log(f"  ❌ {ctx}: GPU failed, stepping down...")
        except Exception as e:
            emit_log(f"  ❌ {ctx}: {e}")
            results[str(ctx)] = {"error": str(e)}

    if not found_full_gpu and not enable_hybrid:
        emit_log("  ⚠️ No context fit on GPU")

    return results


async def measure_cpu_contexts(
    model_path: Path,
    contexts: list[int],
    n_batch: int,
    mmproj_path: str | None,
    emit_log: Callable[[str], None],
    validate_timing: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Measure CPU profiles for requested contexts with timing validation.

    Args:
        model_path: Path to model file
        contexts: List of context lengths to measure (descending order)
        n_batch: Batch size
        mmproj_path: Optional multimodal projector path
        emit_log: Logging callback
        validate_timing: Enable timing monotonicity validation

    Returns:
        Dict mapping context strings to profile dicts
    """
    results: dict[str, dict[str, Any]] = {}
    timing_tracker = TimingTracker() if validate_timing else None

    for ctx in contexts:
        emit_log(f"Measuring CPU context {ctx}...")
        emit_log("  → Loading model in CPU-only mode (n_gpu_layers=0)...")
        try:
            profile = await measure_cpu_context(model_path, ctx, n_batch, mmproj_path)
            if profile.get("success"):
                # Validate timing (shared logic with GPU)
                profile = validate_and_log_timing(
                    profile, ctx, timing_tracker, emit_log
                )

                # Check for timing anomaly - stop measuring if detected
                if profile.get("error"):
                    emit_log(f"  → Stopping - keeping {len(results)} valid contexts")
                    break

                results[str(ctx)] = profile
                log_profile_result(ctx, profile, emit_log)
            else:
                emit_log(f"  ❌ {ctx}: CPU measurement failed")
                if stderr := profile.get("stderr"):
                    emit_log(f"      stderr: {stderr}")
                results[str(ctx)] = {"error": "CPU measurement failed"}
        except Exception as e:
            emit_log(f"  ❌ {ctx}: {e}")
            results[str(ctx)] = {"error": str(e)}

    return results


async def measure_auto_mode(
    model_path: Path,
    contexts: list[int],
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    enable_hybrid: bool,
    emit_log: Callable[[str], None],
    tracker: SubprocessTracker,
    safety_margin: int | None = None,
    validate_timing: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Auto mode: find max GPU context with hybrid fallback.

    Does NOT fall back to CPU if GPU fails - if no GPU layers fit,
    the model requires explicit --cpu mode measurement.
    """
    results = await measure_gpu_with_stepdown(
        model_path,
        contexts,
        n_batch,
        gpu_index,
        mmproj_path,
        enable_hybrid,
        emit_log,
        tracker,
        safety_margin,
        validate_timing,
    )

    # Check if any GPU measurement succeeded
    gpu_succeeded = any(
        r.get("success") and r.get("n_gpu_layers", 0) != 0 for r in results.values()
    )

    if not gpu_succeeded:
        emit_log(
            "⚠️  GPU failed for all contexts. "
            "Use --cpu mode if you want to measure CPU-only configuration."
        )

    return results


def log_profile_result(
    ctx: int, profile: dict[str, Any], emit_log: Callable[[str], None]
) -> None:
    """Log successful profile measurement."""
    vram = profile.get("vram_mb", "N/A")
    ram = profile.get("ram_mb", "N/A")
    layers = profile.get("n_gpu_layers", "N/A")
    emit_log(f"  ✅ {ctx}: VRAM={vram}MB, RAM={ram}MB, layers={layers}")


def _log_hybrid_result(
    ctx: int,
    profile: dict[str, Any],
    n_layers: int,
    total_layers: Any,
    emit_log: Callable[[str], None],
) -> None:
    """Log successful hybrid profile measurement."""
    vram = profile.get("vram_mb", "N/A")
    ram = profile.get("ram_mb", "N/A")
    percent = round(n_layers / total_layers * 100) if total_layers != "?" else "?"
    emit_log(
        f"  ✅ {ctx}: VRAM={vram}MB, RAM={ram}MB, "
        f"layers={n_layers}/{total_layers} ({percent}% on GPU, hybrid)"
    )


async def try_hybrid_measurement(
    model_path: Path,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    min_layers_hint: int | None,
    emit_log: Callable[[str], None],
    tracker: "SubprocessTracker",
    safety_margin: int | None = None,
) -> dict[str, Any] | None:
    """Try hybrid (partial GPU offload) measurement for a context.

    Returns profile dict with n_gpu_layers set to optimal partial value,
    or None if even partial offload fails.
    """
    emit_log(f"  ❌ {context}: Full GPU failed (OOM)")
    emit_log("  → Trying partial GPU offload (hybrid mode)...")
    if min_layers_hint:
        emit_log(
            f"  → Starting binary search from {min_layers_hint} layers "
            "(known to fit from larger context)..."
        )
    else:
        emit_log("  → Running binary search to find max layers that fit...")
    try:
        profile = await measure_hybrid_context(
            model_path,
            context,
            n_batch,
            gpu_index,
            mmproj_path,
            min_layers_hint,
            tracker,
            emit_log,
            safety_margin,
        )
        if profile.get("success"):
            n_layers = profile.get("n_gpu_layers", 0)
            total_layers = profile.get("total_layers", "?")
            _log_hybrid_result(context, profile, n_layers, total_layers, emit_log)
            return profile
        error_msg = profile.get("error") or "Hybrid also failed"
        emit_log(f"  ❌ {context}: {error_msg}")
    except Exception as e:
        emit_log(f"  ❌ {context}: Hybrid failed: {e}")
    return None


def apply_resource_caps(
    results: dict[str, dict[str, Any]],
    vram_cap_mb: int | None,
    ram_cap_mb: int | None,
    emit_log: Callable[[str], None],
) -> None:
    """Apply resource caps to measurement results.

    Marks profiles that exceed caps with 'exceeds_cap' field.
    This influences which contexts are considered to "fit" for activation.
    """
    if vram_cap_mb is None and ram_cap_mb is None:
        return  # No caps to apply

    cap_info = []
    if vram_cap_mb is not None:
        cap_info.append(f"VRAM≤{vram_cap_mb}MB")
    if ram_cap_mb is not None:
        cap_info.append(f"RAM≤{ram_cap_mb}MB")
    emit_log(f"Applying resource caps: {', '.join(cap_info)}")

    for ctx_str, profile in results.items():
        if profile.get("error") or not profile.get("success", True):
            continue

        vram_mb = profile.get("vram_mb", 0)
        ram_mb = profile.get("ram_mb", 0)

        exceeds_vram = vram_cap_mb is not None and vram_mb > vram_cap_mb
        exceeds_ram = ram_cap_mb is not None and ram_mb > ram_cap_mb

        if exceeds_vram or exceeds_ram:
            reasons = []
            if exceeds_vram:
                reasons.append(f"VRAM {vram_mb}MB > {vram_cap_mb}MB")
            if exceeds_ram:
                reasons.append(f"RAM {ram_mb}MB > {ram_cap_mb}MB")
            profile["exceeds_cap"] = True
            profile["cap_exceeded_reason"] = "; ".join(reasons)
            emit_log(f"  ⚠️ {ctx_str}: exceeds cap ({', '.join(reasons)})")
        else:
            profile["exceeds_cap"] = False
