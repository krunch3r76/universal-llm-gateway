"""
vLLM measurement execution.

Spawns vllm_memory_test.py subprocess for each context size to measure
VRAM/RAM usage. vLLM only supports GPU mode (no CPU/hybrid).

Unlike GGUF measurement which uses n_gpu_layers and binary search,
vLLM measurement varies max_model_len and reports resource usage.
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
    setup_measurement_subprocess,
)

logger = get_logger(__name__)

# vLLM models can be slow to load (large transformer weights + CUDA graph capture)
_DEFAULT_TIMEOUT_SEC = 600


def _resolve_test_script() -> Path:
    """Locate vllm_memory_test.py."""
    root = Path(__file__).resolve()
    project_root = root.parents[5]
    return (
        project_root
        / "libs"
        / "inference_djinn"
        / "scripts"
        / "tests"
        / "vllm"
        / "vllm_memory_test.py"
    )


async def run_vllm_probe(
    model_path: Path,
    max_model_len: int,
    quantization: str | None,
    gpu_memory_utilization: float,
    tracker: SubprocessTracker,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run vllm_memory_test.py subprocess for a single context size.

    Returns dict with success, vram_mb, ram_mb, n_gpu_layers=-1.
    """
    script = _resolve_test_script()
    if not script.exists():
        return {"success": False, "error": f"vLLM test script not found: {script}"}

    cmd = [
        sys.executable,
        str(script),
        "--model",
        str(model_path),
        "--max-model-len",
        str(max_model_len),
        "--gpu-memory-utilization",
        str(gpu_memory_utilization),
    ]
    if quantization:
        cmd.extend(["--quantization", quantization])

    nice_value = env_int("MEASUREMENT_SUBPROC_NICE") or DEFAULT_SUBPROC_NICE
    oom_adj = (
        env_int("MEASUREMENT_SUBPROC_OOM_SCORE_ADJ") or DEFAULT_SUBPROC_OOM_SCORE_ADJ
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=os.environ.copy(),
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
        "Started vLLM measurement PID %s: max_model_len=%d, quant=%s, "
        "gpu_mem=%.2f, timeout=%ds",
        proc.pid,
        max_model_len,
        quantization,
        gpu_memory_utilization,
        timeout_sec,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        logger.warning("vLLM probe timeout after %ds, killing subprocess", timeout_sec)
        try:
            if proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        return {"success": False, "error": f"timeout after {timeout_sec}s"}
    finally:
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

    data["stderr"] = stderr.decode().strip()
    data["n_gpu_layers"] = -1  # vLLM always uses full GPU
    data["max_model_len"] = max_model_len
    return data


async def measure_vllm_contexts(
    model_path: Path,
    contexts: list[int],
    quantization: str | None,
    gpu_memory_utilization: float,
    emit_log: Callable[[str], None],
    tracker: SubprocessTracker,
) -> dict[str, dict[str, Any]]:
    """
    Measure vLLM GPU profiles, stepping down contexts until one fits.

    vLLM only supports full GPU (no CPU/hybrid). Each context spawns a fresh
    subprocess that loads the model with max_model_len=context and reports
    VRAM/RAM usage.
    """
    results: dict[str, dict[str, Any]] = {}

    for ctx in contexts:
        emit_log(f"Measuring vLLM context {ctx} (max_model_len={ctx})...")
        emit_log(
            f"  → Loading with gpu_memory_utilization={gpu_memory_utilization}, "
            f"quantization={quantization or 'auto-detect'}"
        )

        profile = await run_vllm_probe(
            model_path,
            ctx,
            quantization,
            gpu_memory_utilization,
            tracker,
        )

        if profile.get("success"):
            vram = profile.get("vram_mb", "N/A")
            ram = profile.get("ram_mb", "N/A")
            emit_log(f"  ✅ {ctx}: VRAM={vram}MB, RAM={ram}MB")
            results[str(ctx)] = profile
        else:
            error = profile.get("error", "unknown")
            emit_log(f"  ❌ {ctx}: {error}")
            results[str(ctx)] = {"error": error}

    return results
