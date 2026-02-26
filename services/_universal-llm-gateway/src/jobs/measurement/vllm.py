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

    Returns dict with success, vram_mb, ram_mb, max_model_len, and
    n_gpu_layers=-1 (sentinel used by callers to classify device as "gpu";
    stripped from catalog entries by _build_updated_catalog_entry since it
    has no runtime meaning for the vLLM engine).
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



# ---------------------------------------------------------------------------
# gpu_memory_utilization heuristics
# ---------------------------------------------------------------------------

_UTIL_STEP = 0.05
_UTIL_MAX_PROBES = 10

# CUDA context initialisation + vLLM worker/engine internals.
_CHAT_SYSTEM_OVERHEAD_BYTES: int = 1 * 1024**3
# Conservative upper bound on KV cost per token across common architectures.
# Per-token KV ≈ 2 × layers × kv_heads × head_dim × fp16.
# 32B-class models ≈ 256 KB/token; smaller models are lower.
# Using 256 KB here is intentionally generous so we never start too low.
_KV_BYTES_PER_TOKEN: int = 256 * 1024


def _model_dir_size_bytes(model_path: Path) -> int:
    """Sum file sizes in model directory."""
    return sum(f.stat().st_size for f in model_path.rglob("*") if f.is_file())


def _compute_initial_utilization(
    model_path: Path,
    total_vram_bytes: int,
    max_model_len: int = 0,
) -> float:
    """Compute starting gpu_memory_utilization for a probe at max_model_len.

    Floor = model weights + system overhead + estimated KV for max_model_len,
    rounded up to the nearest _UTIL_STEP.  Passing max_model_len=0 (default,
    used for embeddings) omits the KV term so embeddings start as low as
    possible.
    """
    model_bytes = _model_dir_size_bytes(model_path)
    kv_bytes = _KV_BYTES_PER_TOKEN * max_model_len
    needed = model_bytes + _CHAT_SYSTEM_OVERHEAD_BYTES + kv_bytes
    fraction = needed / total_vram_bytes
    rounded = math.ceil(fraction / _UTIL_STEP) * _UTIL_STEP
    return max(0.10, min(0.95, round(rounded, 2)))


async def find_min_gpu_utilization(
    model_path: Path,
    max_model_len: int,
    quantization: str | None,
    tracker: SubprocessTracker,
    emit_log: Callable[[str], None],
    loader_config: dict[str, Any] | None = None,
    device_index: int = 0,
    util_cap: float = 0.95,
    is_embedding: bool = False,
) -> tuple[float, dict[str, Any]]:
    """Find minimum gpu_memory_utilization that loads the model at max_model_len.

    Starting utilization = model weights + system overhead + estimated KV for
    max_model_len (omitted when is_embedding=True — embeddings have no KV cache).
    Probes upward in _UTIL_STEP increments until vLLM loads successfully.

    Returns (utilization, profile_dict).
    Raises RuntimeError if no utilization up to util_cap succeeds.
    """
    total_vram = get_total_vram_bytes(device_index)
    if total_vram is None:
        raise RuntimeError("Cannot query total VRAM via pynvml")

    kv_len = 0 if is_embedding else max_model_len
    initial = _compute_initial_utilization(model_path, total_vram, kv_len)
    total_vram_gb = total_vram / (1024**3)
    model_gb = _model_dir_size_bytes(model_path) / (1024**3)
    weight_pct = model_gb / total_vram_gb * 100

    emit_log(
        f"  Model: {model_gb:.1f}GB ({weight_pct:.0f}% of "
        f"{total_vram_gb:.0f}GB VRAM) → starting at {initial:.2f}"
    )

    util = initial
    for attempt in range(_UTIL_MAX_PROBES):
        if util > util_cap:
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
        f"Model failed to load at gpu_memory_utilization up to"
        f" {min(util, util_cap):.2f}"
    )
