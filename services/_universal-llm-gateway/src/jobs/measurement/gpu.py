"""
GPU and hybrid measurement execution.

Handles full GPU offload, partial GPU offload (hybrid), and binary search
for optimal layer count. Spawns llama-server directly — identical code path
to runtime NativeGGUFEngine via ServerConfig.to_cli_args().
"""

import asyncio
import os
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from inference_djinn.engines.gguf.native.binary import find_llama_server
from inference_djinn.engines.gguf.native.config import ServerConfig
from universal_logging import get_logger

from .common import (
    DEFAULT_SUBPROC_NICE,
    DEFAULT_SUBPROC_OOM_SCORE_ADJ,
    SubprocessTracker,
    env_int,
    kill_measurement_process,
    measure_process_tree_rss_mb,
    measure_process_vram_mb,
    poll_health,
    setup_measurement_subprocess,
)
from .gguf_reader import extract_block_count
from .memory_info import maybe_psutil

logger = get_logger(__name__)


def _select_visible_gpu_device(gpu_index: int) -> str:
    """Resolve gpu_index through any existing CUDA_VISIBLE_DEVICES mask.

    Maps gpu_index=0 to the first visible device (not necessarily cuda:0).
    """
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if not cuda_visible:
        return str(gpu_index)

    tokens = [t.strip() for t in cuda_visible.split(",") if t.strip()]
    if not tokens:
        return str(gpu_index)

    if gpu_index < 0 or gpu_index >= len(tokens):
        raise ValueError(
            f"gpu_index {gpu_index} out of range for "
            f"CUDA_VISIBLE_DEVICES={cuda_visible!r}"
        )
    return tokens[gpu_index]


def _compute_adaptive_timeout(
    model_path: Path, n_layers: int, base_timeout: int
) -> int:
    """Reduce timeout for large models to fail fast."""
    psutil_mod = maybe_psutil()
    if not psutil_mod:
        return base_timeout
    try:
        vm = psutil_mod.virtual_memory()
        model_bytes = model_path.stat().st_size
        if model_bytes / vm.total > 0.5:
            timeout = 180 if n_layers == -1 else 90
            logger.info(
                f"Large model ({int(model_bytes / (1024**3))}GB, "
                f"{int(model_bytes / vm.total * 100)}% of RAM), "
                f"timeout: {timeout}s (layers={n_layers})"
            )
            return timeout
    except Exception as e:
        logger.warning(f"Failed to compute adaptive timeout: {e}")
    return base_timeout


async def _start_memory_monitor(
    model_path: Path, proc: asyncio.subprocess.Process
) -> asyncio.Task[None] | None:
    """Start swap-pressure monitor; returns task or None."""
    psutil_mod = maybe_psutil()
    if not psutil_mod:
        return None
    try:
        vm = psutil_mod.virtual_memory()
        if model_path.stat().st_size <= vm.total * 0.5:
            return None
    except Exception:
        return None

    async def _watch() -> None:
        initial_swap = psutil_mod.swap_memory().used
        while True:
            await asyncio.sleep(2)
            delta_mb = (psutil_mod.swap_memory().used - initial_swap) / (1024 * 1024)
            if delta_mb > 2048:
                logger.warning(
                    f"Memory pressure: swap +{int(delta_mb)}MB, killing subprocess"
                )
                try:
                    if proc.pid:
                        os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                break

    return asyncio.create_task(_watch())


async def run_layer_test(
    model_path: Path,
    n_layers: int,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    tracker: SubprocessTracker,
    timeout_sec: int = 300,
    embedding: bool = False,
    pooling: str | None = None,
    ubatch_size: int | None = None,
) -> dict[str, Any]:
    """Spawn llama-server, wait for health, measure VRAM/RAM, kill.

    Uses ServerConfig.to_cli_args() for identical CLI to runtime
    NativeGGUFEngine, and per-process pynvml for VRAM measurement.
    """
    socket_path = f"/tmp/measurement-{uuid4().hex[:8]}.sock"
    config = ServerConfig(
        model_path=str(model_path),
        socket_path=socket_path,
        ctx_size=context,
        n_gpu_layers=n_layers,
        batch_size=n_batch,
        parallel_slots=1,
        continuous_batching=False,
        flash_attn=True,
        mlock=True,
        mmproj_path=mmproj_path,
        verbose=False,
        embedding=embedding,
        pooling=pooling,
        ubatch_size=ubatch_size,
    )

    args = config.to_cli_args()
    args[0] = find_llama_server()

    env = os.environ.copy()
    if n_layers == 0:
        # CPU-only: hide all GPUs to prevent CUDA context initialization
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["HIP_VISIBLE_DEVICES"] = ""
    else:
        env["CUDA_VISIBLE_DEVICES"] = _select_visible_gpu_device(gpu_index)

    adaptive_timeout = _compute_adaptive_timeout(model_path, n_layers, timeout_sec)

    nice_value = env_int("MEASUREMENT_SUBPROC_NICE") or DEFAULT_SUBPROC_NICE
    oom_adj = (
        env_int("MEASUREMENT_SUBPROC_OOM_SCORE_ADJ") or DEFAULT_SUBPROC_OOM_SCORE_ADJ
    )

    socket_file = Path(socket_path)
    if socket_file.exists():
        socket_file.unlink()

    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
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
        "Started llama-server measurement PID %s: layers=%d, ctx=%d, "
        "nice=%d, oom_adj=%d, timeout=%ds",
        proc.pid,
        n_layers,
        context,
        nice_value,
        oom_adj,
        adaptive_timeout,
    )

    monitor_task = await _start_memory_monitor(model_path, proc)

    try:
        error = await poll_health(
            socket_path,
            proc,
            adaptive_timeout,
            poll_interval=1.0,
            process_name="llama-server",
        )
        if error:
            return {"success": False, "error": error}

        vram_mb = measure_process_vram_mb(proc.pid, device_index=gpu_index) or 0
        ram_mb = measure_process_tree_rss_mb(proc.pid)

        return {"success": True, "vram_mb": vram_mb, "ram_mb": ram_mb}
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        tracker.discard(proc)
        await kill_measurement_process(proc, sigterm_timeout=5.0)
        if socket_file.exists():
            try:
                socket_file.unlink()
            except OSError:
                pass


async def measure_gpu_context(
    model_path: Path,
    context: int,
    n_batch: int,
    gpu_index: int,
    mmproj_path: str | None,
    tracker: SubprocessTracker,
    total_layers: int | None = None,
) -> dict[str, Any]:
    """Measure single GPU context with full offload verification.

    Uses explicit layer count instead of -1 to prevent hidden hybrid:
    llama.cpp silently reduces GPU layers when VRAM is insufficient
    with -1, producing successful but misleading measurements.
    Explicit count fails hard on OOM, triggering correct hybrid fallback.
    """
    test_layers = total_layers if total_layers is not None else -1
    profile = await run_layer_test(
        model_path, test_layers, context, n_batch, gpu_index, mmproj_path, tracker
    )
    if profile.get("success"):
        profile["n_gpu_layers"] = -1
        if total_layers is not None:
            profile["total_layers"] = total_layers
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
    total_layers = extract_block_count(model_path)
    if not total_layers:
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
            high = mid - 1

    if best:
        final_layers = best.get("n_gpu_layers", 0)
        emit_log(
            f"  → Binary search complete: max {final_layers}/{total_layers} layers fit"
        )

        if final_layers > 0:
            if safety_margin is None:
                safety_margin = env_int("MEASUREMENT_HYBRID_SAFETY_MARGIN")
            if safety_margin is None:
                safety_margin = 2
            safe_layers = max(1, final_layers - safety_margin)

            if safety_margin > 0 and safe_layers < final_layers:
                emit_log(
                    f"  → Applying -{safety_margin} safety margin: "
                    f"{final_layers} → {safe_layers} layers"
                )
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
                        f"  → Verification at {safe_layers} layers "
                        f"failed ({error}), using {final_layers} layers"
                    )

        return best

    emit_log("  → Binary search failed: no configuration fits")
    return {"success": False, "error": "Hybrid search failed"}
