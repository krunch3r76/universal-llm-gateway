"""
vLLM measurement execution via vllm serve.

Starts a temporary vllm serve process for each context size, polls /health
until the model is loaded and KV cache allocated, then measures per-process
VRAM and process-tree RSS. Identical code path to runtime VLLMServerEngine.
"""

import asyncio
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from inference_djinn.engines.vllm.server.config import VLLMServerConfig
from inference_djinn.engines.vllm.server.manager import build_vllm_command
from universal_logging import get_logger

from .common import (
    DEFAULT_SUBPROC_NICE,
    DEFAULT_SUBPROC_OOM_SCORE_ADJ,
    SubprocessTracker,
    env_int,
    get_total_vram_bytes,
    kill_measurement_process,
    measure_process_tree_rss_mb,
    measure_process_vram_mb,
    poll_health,
    setup_measurement_subprocess,
)

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SEC = 600

# Gateway-internal keys that must not reach vllm serve CLI.
# Structured VLLMServerConfig fields are extracted explicitly;
# these are stripped from the remainder before extra_cli_args.
_GATEWAY_INTERNAL_KEYS = frozenset(
    {
        "warmup",
        "trust_remote_code",
        "embedding",
        "embedding_task_default",
        "embedding_task_prefixes",
        "n_ctx",
    }
)

# Keys extracted into VLLMServerConfig structured fields.
_STRUCTURED_CONFIG_KEYS = frozenset(
    {
        "gpu_memory_utilization",
        "quantization",
        "max_model_len",
        "dtype",
    }
)


def _build_measurement_config(
    model_path: Path,
    max_model_len: int,
    quantization: str | None,
    gpu_memory_utilization: float,
    loader_config: dict[str, Any] | None,
) -> VLLMServerConfig:
    """Build VLLMServerConfig for a measurement probe."""
    socket_path = f"/tmp/vllm-measurement-{uuid4().hex[:8]}.sock"

    dtype = "auto"
    extra_cli_args: dict[str, Any] = {}

    if loader_config:
        dtype = loader_config.get("dtype", "auto")
        skip_keys = _GATEWAY_INTERNAL_KEYS | _STRUCTURED_CONFIG_KEYS
        extra_cli_args = {k: v for k, v in loader_config.items() if k not in skip_keys}

    is_embedding = (loader_config or {}).get("embedding") is True
    if is_embedding:
        extra_cli_args["runner"] = "pooling"

    return VLLMServerConfig(
        model_path=str(model_path),
        socket_path=socket_path,
        enable_auto_tool_choice=False,
        tool_call_parser=None,
        max_model_len=max_model_len,
        gpu_memory_utilization=gpu_memory_utilization,
        quantization=quantization,
        dtype=dtype,
        extra_cli_args=extra_cli_args,
    )


async def run_vllm_probe(
    model_path: Path,
    max_model_len: int,
    quantization: str | None,
    gpu_memory_utilization: float,
    tracker: SubprocessTracker,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
    loader_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start vllm serve, wait for health, measure VRAM/RAM, kill.

    Returns dict with success, vram_mb, ram_mb, max_model_len, n_gpu_layers=-1.
    Uses identical code path to runtime VLLMServerEngine.
    """
    config = _build_measurement_config(
        model_path,
        max_model_len,
        quantization,
        gpu_memory_utilization,
        loader_config,
    )
    cmd = build_vllm_command(config)
    env = config.to_subprocess_env()

    nice_value = env_int("MEASUREMENT_SUBPROC_NICE") or DEFAULT_SUBPROC_NICE
    oom_adj = (
        env_int("MEASUREMENT_SUBPROC_OOM_SCORE_ADJ") or DEFAULT_SUBPROC_OOM_SCORE_ADJ
    )

    if config.socket_path is None:
        raise RuntimeError(
            "VLLMServerConfig.socket_path must be set for measurement probes"
        )
    socket_file = Path(config.socket_path)
    if socket_file.exists():
        socket_file.unlink()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
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
        "Started vllm serve measurement PID %s: max_model_len=%d, quant=%s, "
        "gpu_mem=%.2f, timeout=%ds",
        proc.pid,
        max_model_len,
        quantization,
        gpu_memory_utilization,
        timeout_sec,
    )

    try:
        error = await poll_health(
            config.socket_path,
            proc,
            timeout_sec,
            poll_interval=2.0,
            process_name="vllm serve",
        )
        if error:
            return {"success": False, "error": error}

        vram_mb = measure_process_vram_mb(proc.pid)
        ram_mb = measure_process_tree_rss_mb(proc.pid)

        return {
            "success": True,
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
            "max_model_len": max_model_len,
            "n_gpu_layers": -1,
        }
    finally:
        tracker.discard(proc)
        # vLLM shutdown is slower than llama-server (CUDA cleanup + EngineCore fork)
        await kill_measurement_process(proc, sigterm_timeout=30.0)
        if socket_file.exists():
            try:
                socket_file.unlink()
            except OSError:
                pass


async def measure_vllm_contexts(
    model_path: Path,
    contexts: list[int],
    quantization: str | None,
    gpu_memory_utilization: float,
    emit_log: Callable[[str], None],
    tracker: SubprocessTracker,
    loader_config: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Measure vLLM GPU profiles, stepping down contexts until one fits.

    vLLM only supports full GPU (no CPU/hybrid). Each context spawns a fresh
    vllm serve process that loads the model with max_model_len=context and
    reports VRAM/RAM usage. Catalog loader fields are forwarded so measurement
    uses the same parameters as the runtime engine.
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
            loader_config=loader_config,
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


# ---------------------------------------------------------------------------
# Embedding model: minimum gpu_memory_utilization search
# ---------------------------------------------------------------------------

_UTIL_STEP = 0.05
_UTIL_OVERHEAD_FRACTION = 0.08
_UTIL_MAX_PROBES = 10


def _model_dir_size_bytes(model_path: Path) -> int:
    """Sum file sizes in model directory."""
    return sum(f.stat().st_size for f in model_path.rglob("*") if f.is_file())


def _compute_initial_utilization(
    model_path: Path,
    total_vram_bytes: int,
) -> float:
    """Compute starting gpu_memory_utilization from model size vs GPU VRAM.

    weight_fraction = model_dir_size / total_vram, then add overhead
    for CUDA context and vLLM internals, rounded up to nearest 0.05.
    """
    model_bytes = _model_dir_size_bytes(model_path)
    weight_fraction = model_bytes / total_vram_bytes
    initial = weight_fraction + _UTIL_OVERHEAD_FRACTION
    rounded = math.ceil(initial / _UTIL_STEP) * _UTIL_STEP
    return max(0.10, min(0.90, round(rounded, 2)))


async def find_min_gpu_utilization(
    model_path: Path,
    max_model_len: int,
    quantization: str | None,
    tracker: SubprocessTracker,
    emit_log: Callable[[str], None],
    loader_config: dict[str, Any] | None = None,
    device_index: int = 0,
) -> tuple[float, dict[str, Any]]:
    """Find minimum gpu_memory_utilization for a vLLM embedding model.

    Computes model-weight fraction of total VRAM, adds overhead, then
    probes upward in 0.05 steps until vLLM loads successfully.

    Returns (utilization, profile_dict).
    Raises RuntimeError if no utilization up to 0.90 succeeds.
    """
    total_vram = get_total_vram_bytes(device_index)
    if total_vram is None:
        raise RuntimeError("Cannot query total VRAM via pynvml")

    initial = _compute_initial_utilization(model_path, total_vram)
    total_vram_gb = total_vram / (1024**3)
    model_gb = _model_dir_size_bytes(model_path) / (1024**3)
    weight_pct = model_gb / total_vram_gb * 100

    emit_log(
        f"  Model: {model_gb:.1f}GB ({weight_pct:.0f}% of "
        f"{total_vram_gb:.0f}GB VRAM) → starting at {initial:.2f}"
    )

    util = initial
    for attempt in range(_UTIL_MAX_PROBES):
        if util > 0.90:
            break
        pool_gb = total_vram_gb * util
        emit_log(
            f"  → Probe {attempt + 1}: "
            f"gpu_memory_utilization={util:.2f} ({pool_gb:.1f}GB)..."
        )
        profile = await run_vllm_probe(
            model_path,
            max_model_len,
            quantization,
            util,
            tracker,
            loader_config=loader_config,
        )
        if profile.get("success"):
            vram_mb = profile.get("vram_mb", "N/A")
            emit_log(f"  ✅ Found: {util:.2f} (VRAM={vram_mb}MB)")
            return util, profile
        error = profile.get("error", "unknown")
        emit_log(f"  ❌ {util:.2f} failed: {error}")
        util = round(util + _UTIL_STEP, 2)

    raise RuntimeError(
        f"Model failed to load at gpu_memory_utilization up to {min(util, 0.90):.2f}"
    )
