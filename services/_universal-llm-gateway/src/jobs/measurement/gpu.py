"""
GPU and hybrid measurement execution.

Handles full GPU offload, partial GPU offload (hybrid), and binary search
for optimal layer count. Uses subprocess isolation for memory safety.
"""

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from inference_djinn.scripts.config_generators.gguf.utils import (
    extract_metadata,
    to_native_int,
)
from universal_logging import get_logger

from .common import (
    DEFAULT_SUBPROC_NICE,
    DEFAULT_SUBPROC_OOM_SCORE_ADJ,
    SubprocessTracker,
    compute_ram_headroom_bytes,
    env_int,
    maybe_psutil,
    setup_measurement_subprocess,
)

logger = get_logger(__name__)


def _resolve_test_script() -> Path:
    """Locate llama_server_measurement.py."""
    root = Path(__file__).resolve()
    # services/_universal-llm-gateway/src/jobs/measurement/gpu.py
    # -> project root at parents[5]
    project_root = root.parents[5]
    return (
        project_root
        / "libs"
        / "inference_djinn"
        / "scripts"
        / "tests"
        / "gguf"
        / "llama_server_measurement.py"
    )


async def run_layer_test(
    model_path: Path,
    n_layers: int,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    tracker: SubprocessTracker,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Run llama_server_measurement asynchronously and return parsed result."""
    script = _resolve_test_script()
    if not script.exists():
        return {"success": False, "error": f"Test script not found: {script}"}

    # Host-safety: Check RAM headroom, use adaptive timeouts for large models.
    psutil_mod = maybe_psutil()
    headroom_bytes = compute_ram_headroom_bytes(psutil_mod)
    adaptive_timeout = timeout_sec
    vm = None

    if psutil_mod and headroom_bytes is not None:
        try:
            vm = psutil_mod.virtual_memory()
            available = vm.available
            if available < headroom_bytes:
                return {
                    "success": False,
                    "error": (
                        "Insufficient RAM headroom for safe measurement probe "
                        f"(available={available // (1024 * 1024)}MB, "
                        f"headroom={headroom_bytes // (1024 * 1024)}MB)"
                    ),
                }

            # For huge models, use adaptive timeouts
            try:
                model_bytes = model_path.stat().st_size
                model_to_ram_ratio = model_bytes / vm.total
                if model_to_ram_ratio > 0.5:
                    if n_layers == -1:
                        adaptive_timeout = 180
                    else:
                        adaptive_timeout = 90
                    logger.info(
                        f"Large model detected "
                        f"({int(model_bytes/(1024*1024*1024))}GB, "
                        f"{int(model_to_ram_ratio*100)}% of RAM), "
                        f"using timeout: {adaptive_timeout}s (layers={n_layers})"
                    )
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"Failed to compute measurement subprocess limits: {e}")

    cmd = [
        sys.executable,
        str(script),
        "--model",
        str(model_path),
        "--layers",
        str(n_layers),
        "--ctx",
        str(context),
        "--batch",
        str(n_batch),
        "--gpu-index",
        str(gpu_index),
        "--mode",
        "gpu",
    ]
    if mmproj_path:
        cmd.extend(["--mmproj", mmproj_path])

    nice_value = env_int("MEASUREMENT_SUBPROC_NICE") or DEFAULT_SUBPROC_NICE
    oom_adj = (
        env_int("MEASUREMENT_SUBPROC_OOM_SCORE_ADJ") or DEFAULT_SUBPROC_OOM_SCORE_ADJ
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=lambda: setup_measurement_subprocess(
            as_limit_bytes=None,
            nice_value=nice_value,
            oom_score_adj=oom_adj,
        ),
    )
    tracker.add(proc)
    logger.info(
        f"Started subprocess PID {proc.pid} with safety: "
        f"nice={nice_value}, oom_adj={oom_adj}, timeout={adaptive_timeout}s"
    )

    # Monitor memory pressure for large models
    monitor_task = None
    if psutil_mod and vm is not None and model_path.exists():
        try:
            model_bytes = model_path.stat().st_size
            if model_bytes > vm.total * 0.5:

                async def monitor_memory_pressure() -> None:
                    """Kill subprocess if swap usage spikes."""
                    initial_swap = psutil_mod.swap_memory().used
                    while True:
                        await asyncio.sleep(2)
                        current_swap = psutil_mod.swap_memory().used
                        swap_delta_mb = (current_swap - initial_swap) / (1024 * 1024)
                        if swap_delta_mb > 2048:
                            logger.warning(
                                f"Memory pressure detected: "
                                f"swap +{int(swap_delta_mb)}MB, "
                                "killing probe subprocess"
                            )
                            try:
                                if proc.pid:
                                    os.killpg(proc.pid, signal.SIGKILL)
                            except Exception:
                                pass
                            break

                monitor_task = asyncio.create_task(monitor_memory_pressure())
        except Exception as e:
            logger.debug(f"Could not start memory pressure monitor: {e}")

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=adaptive_timeout
        )
    except TimeoutError:
        logger.warning(
            f"Probe timeout after {adaptive_timeout}s "
            "(possible memory pressure freeze), killing subprocess"
        )
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        tracker.discard(proc)
        return {"success": False, "error": f"timeout after {adaptive_timeout}s"}
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        tracker.discard(proc)

    if proc.returncode != 0:
        return {
            "success": False,
            "error": f"exit {proc.returncode}",
            "stderr": stderr.decode().strip(),
        }

    output = stdout.decode().strip()
    if not output:
        return {"success": False, "error": "no output"}

    try:
        data = json.loads(output)
    except Exception as e:
        return {
            "success": False,
            "error": f"invalid json: {e}",
            "stderr": stderr.decode().strip(),
        }

    # Include stderr in result for timing extraction
    data["stderr"] = stderr.decode().strip()
    return data


async def measure_gpu_context(
    model_path: Path,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    tracker: SubprocessTracker,
) -> dict[str, Any]:
    """Measure single GPU context via async subprocess."""
    profile = await run_layer_test(
        model_path, -1, context, n_batch, gpu_index, mmproj_path, tracker
    )
    if profile.get("success"):
        profile.setdefault("n_gpu_layers", -1)
    return profile


async def measure_hybrid_context(
    model_path: Path,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    min_layers_hint: int | None,
    tracker: SubprocessTracker,
    emit_log: Callable[[str], None],
    safety_margin: int | None = None,
) -> dict[str, Any]:
    """Measure single hybrid context using async binary search."""
    meta, _ = extract_metadata(str(model_path))
    total_layers = (
        to_native_int(meta.block_count)
        if meta and hasattr(meta, "block_count")
        else None
    )
    if not total_layers or total_layers <= 0:
        return {
            "success": False,
            "error": "Cannot determine total layers from metadata",
        }

    low = min_layers_hint or 0
    high = total_layers
    best: dict[str, Any] | None = None
    iteration = 0

    while low <= high:
        iteration += 1
        mid = (low + high + 1) // 2
        emit_log(
            f"  → Binary search iteration {iteration}: "
            f"testing {mid}/{total_layers} layers (range: {low}-{high})..."
        )
        profile = await run_layer_test(
            model_path,
            mid,
            context,
            n_batch,
            gpu_index,
            mmproj_path,
            tracker,
        )
        success = profile.get("success", False)
        if success:
            profile["n_gpu_layers"] = mid
            profile["total_layers"] = total_layers
            best = profile
            emit_log(f"  → {mid} layers FIT, searching higher...")
            low = mid + 1
        else:
            error = profile.get("error", "unknown")
            emit_log(f"  → {mid} layers FAILED ({error}), searching lower...")
            if stderr := profile.get("stderr"):
                emit_log(f"      stderr: {stderr}")
            high = mid - 1

    if best:
        final_layers = best.get("n_gpu_layers", 0)

        # Apply safety margin for first hybrid context
        if min_layers_hint is None and final_layers > 0:
            if safety_margin is None:
                safety_margin = env_int("MEASUREMENT_HYBRID_SAFETY_MARGIN")
            if safety_margin is None:
                safety_margin = 2
            safe_layers = max(1, final_layers - safety_margin)
            emit_log(
                f"  → Binary search complete: "
                f"max {final_layers}/{total_layers} layers fit"
            )
            if safety_margin > 0:
                emit_log(
                    f"  → Applying -{safety_margin} safety margin (first hybrid): "
                    f"{final_layers} → {safe_layers} layers"
                )

                # Verify safe layer count
                emit_log(
                    f"  → Measuring {safe_layers} layers to verify resource usage..."
                )
                verified = await run_layer_test(
                    model_path,
                    safe_layers,
                    context,
                    n_batch,
                    gpu_index,
                    mmproj_path,
                    tracker,
                )

                if verified.get("success"):
                    verified["n_gpu_layers"] = safe_layers
                    verified["total_layers"] = total_layers
                    emit_log(
                        f"  → Verified: {safe_layers}/{total_layers} layers "
                        f"VRAM={verified.get('vram_mb')}MB, "
                        f"RAM={verified.get('ram_mb')}MB"
                    )
                    return verified
                else:
                    error = verified.get("error", "unknown")
                    emit_log(
                        f"  → Warning: verification at {safe_layers} layers "
                        f"failed ({error}), using {final_layers} layer measurement"
                    )
                    best["n_gpu_layers"] = final_layers
                    return best
            else:
                emit_log(
                    "  → Using max layers without verification (safety margin=0)"
                )
                return best
        else:
            if min_layers_hint is not None:
                emit_log(
                    f"  → Binary search complete: "
                    f"max {final_layers}/{total_layers} layers fit"
                )
                emit_log(
                    "  → No safety margin applied (subsequent hybrid, comfortable fit)"
                )
            else:
                emit_log(
                    f"  → Binary search complete: "
                    f"max {final_layers}/{total_layers} layers fit"
                )

        return best

    emit_log("  → Binary search failed: no configuration fits")
    return {"success": False, "error": "Hybrid search failed"}


def log_hybrid_result(
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
    tracker: SubprocessTracker,
    safety_margin: int | None = None,
) -> dict[str, Any] | None:
    """
    Try hybrid (partial GPU offload) measurement for a context.

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
            log_hybrid_result(context, profile, n_layers, total_layers, emit_log)
            return profile
        error_msg = profile.get("error") or "Hybrid also failed"
        emit_log(f"  ❌ {context}: {error_msg}")
    except Exception as e:
        emit_log(f"  ❌ {context}: Hybrid failed: {e}")
    return None
