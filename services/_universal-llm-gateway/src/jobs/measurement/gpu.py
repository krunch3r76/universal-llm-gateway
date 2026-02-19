"""
GPU and hybrid measurement execution.

Handles full GPU offload, partial GPU offload (hybrid), and binary search
for optimal layer count. Uses subprocess isolation for memory safety.

VRAM is measured host-side via Stargate federation (Edge → Master pynvml).
The subprocess loads the model in --hold mode; Gateway takes a VRAM delta
via Stargate before and after load, then signals the subprocess to continue.
"""

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .common import (
    DEFAULT_SUBPROC_NICE,
    DEFAULT_SUBPROC_OOM_SCORE_ADJ,
    SubprocessTracker,
    env_int,
    maybe_psutil,
    setup_measurement_subprocess,
)
from .gguf_reader import extract_block_count

logger = get_logger(__name__)

STARGATE_VRAM_ENDPOINT = "/api/v1/federation/measurement/vram"


def _resolve_test_script() -> Path:
    """Locate llama_server_measurement.py."""
    root = Path(__file__).resolve()
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


def _stargate_socket_path() -> str | None:
    """Get Edge Stargate Unix socket from env."""
    return os.environ.get("STARGATE_UNIX_SOCKET") or os.environ.get(
        "STARGATE_SOCKET_PATH"
    )


async def _request_vram_snapshot(device_index: int = 0) -> int | None:
    """Request host-side VRAM snapshot via Edge Stargate → Master.

    Returns total per-process VRAM in MB, or None if unavailable.
    """
    socket_path = _stargate_socket_path()
    if not socket_path:
        logger.warning("No STARGATE_UNIX_SOCKET — cannot measure VRAM via federation")
        return None

    import httpx

    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
        resp = await client.post(
            f"http://localhost{STARGATE_VRAM_ENDPOINT}",
            json={"device_index": device_index},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("total_mb")


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
) -> dict[str, Any]:
    """Run llama_server_measurement with host-side VRAM via Stargate.

    Flow: take VRAM baseline → spawn --hold subprocess → wait READY →
    take VRAM snapshot → compute delta → close stdin → read JSON result.
    Falls back to in-container measurement if Stargate is unreachable.
    """
    script = _resolve_test_script()
    if not script.exists():
        return {"success": False, "error": f"Test script not found: {script}"}

    adaptive_timeout = _compute_adaptive_timeout(model_path, n_layers, timeout_sec)

    # VRAM baseline (host-side via federation)
    vram_baseline: int | None = None
    use_hold_mode = False
    try:
        vram_baseline = await _request_vram_snapshot(gpu_index)
        use_hold_mode = vram_baseline is not None
    except Exception as e:
        logger.warning(f"VRAM baseline via Stargate failed, falling back: {e}")

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
    if use_hold_mode:
        cmd.append("--hold")

    nice_value = env_int("MEASUREMENT_SUBPROC_NICE") or DEFAULT_SUBPROC_NICE
    oom_adj = (
        env_int("MEASUREMENT_SUBPROC_OOM_SCORE_ADJ") or DEFAULT_SUBPROC_OOM_SCORE_ADJ
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=os.environ.copy(),
        stdin=asyncio.subprocess.PIPE if use_hold_mode else asyncio.subprocess.DEVNULL,
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
        f"Started subprocess PID {proc.pid}: "
        f"nice={nice_value}, oom_adj={oom_adj}, timeout={adaptive_timeout}s, "
        f"hold={use_hold_mode}"
    )

    monitor_task = await _start_memory_monitor(model_path, proc)

    try:
        if use_hold_mode:
            data = await _run_hold_mode(
                proc, gpu_index, vram_baseline, adaptive_timeout
            )
        else:
            data = await _run_legacy_mode(proc, adaptive_timeout)
    except TimeoutError:
        logger.warning(f"Probe timeout after {adaptive_timeout}s, killing subprocess")
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        return {"success": False, "error": f"timeout after {adaptive_timeout}s"}
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        tracker.discard(proc)

    return data


async def _run_hold_mode(
    proc: asyncio.subprocess.Process,
    gpu_index: int,
    vram_baseline: int | None,
    timeout: int,
) -> dict[str, Any]:
    """Hold-mode flow: wait READY, measure VRAM delta, release."""
    assert proc.stdout is not None
    assert proc.stdin is not None

    ready_line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not ready_line.strip().startswith(b"READY"):
        stderr = await proc.stderr.read() if proc.stderr else b""
        return {
            "success": False,
            "error": f"Expected READY, got: {ready_line.decode().strip()}",
            "stderr": stderr.decode().strip(),
        }

    # Take host-side VRAM snapshot after model load
    vram_mb: int | None = None
    try:
        vram_after = await _request_vram_snapshot(gpu_index)
        if vram_after is not None and vram_baseline is not None:
            vram_mb = max(0, vram_after - vram_baseline)
            logger.info(f"VRAM delta: {vram_after} - {vram_baseline} = {vram_mb} MB")
    except Exception as e:
        logger.error(f"VRAM snapshot after load failed: {e}")

    # Release subprocess: close stdin → triggers warmup + RAM measurement
    proc.stdin.close()

    stdout_rest, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        return {
            "success": False,
            "error": f"exit {proc.returncode}",
            "stderr": stderr.decode().strip(),
        }

    output = stdout_rest.decode().strip()
    return _parse_output(output, stderr.decode().strip(), vram_mb)


async def _run_legacy_mode(
    proc: asyncio.subprocess.Process, timeout: int
) -> dict[str, Any]:
    """Legacy flow (no Stargate): run to completion, use in-container VRAM."""
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

    if proc.returncode != 0:
        return {
            "success": False,
            "error": f"exit {proc.returncode}",
            "stderr": stderr.decode().strip(),
        }

    output = stdout.decode().strip()
    return _parse_output(output, stderr.decode().strip(), vram_override=None)


def _parse_output(
    output: str, stderr: str, vram_override: int | None
) -> dict[str, Any]:
    """Parse JSON from subprocess stdout, optionally override vram_mb."""
    if not output:
        return {"success": False, "error": "no output"}

    try:
        data = json.loads(output)
    except Exception as e:
        return {"success": False, "error": f"invalid json: {e}", "stderr": stderr}

    if vram_override is not None:
        data["vram_mb"] = vram_override

    data["stderr"] = stderr
    return data


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
