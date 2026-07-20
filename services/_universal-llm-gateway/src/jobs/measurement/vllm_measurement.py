"""vLLM-specific VRAM measurement probes for chat and embedding models."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from ...core.events import get_event_bus
from ...core.events.measurement import MeasurementEmbeddingDetected
from .context_selection import resolve_embedding_task_default
from .execution import SubprocessTracker
from .request import MeasureJobRequest
from .vllm import find_min_gpu_utilization

logger = get_logger(__name__)


async def measure_vllm_profiles(
    request: MeasureJobRequest,
    model_path: Path,
    tracker: SubprocessTracker,
    entry: dict[str, Any] | None,
    emit_log: Callable[[str], None],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Run vLLM-specific measurement (GPU only, no hybrid).

    Returns:
        Tuple of (profile_results, loader_updates_to_persist)
    """
    if request.mode == "cpu":
        raise RuntimeError("vLLM does not support CPU-only measurement")

    emit_log("  Engine: vLLM (GPU-only, no hybrid)")

    model_format = (entry or {}).get("metadata", {}).get("format")
    loader_config = (entry or {}).get("loader", {})

    quantization = model_format if model_format in ("awq", "gptq") else None

    emit_log(f"  Quantization: {quantization or 'none'}")

    is_embedding = (entry or {}).get("loader", {}).get("embedding") is True
    if is_embedding:
        ctx = request.training_context_length
        if not ctx:
            raise RuntimeError(
                f"Embedding model '{request.model_id}' has no "
                "training_context_length; cannot determine single probe size."
            )
        emit_log(
            f"  Embedding model: finding minimum gpu_memory_utilization "
            f"at context {ctx}"
        )
        await get_event_bus().publish_nowait(
            MeasurementEmbeddingDetected(model_id=request.model_id, context_length=ctx)
        )
        gpu_mem_util, profile = await find_min_gpu_utilization(
            model_path,
            ctx,
            quantization,
            tracker,
            emit_log,
            loader_config=loader_config,
            device_index=request.gpu_index,
            is_embedding=True,
        )
        results: dict[str, dict[str, Any]] = {str(ctx): profile}
    else:
        contexts_to_measure = request.contexts or [32768, 16384, 8192, 4096]
        emit_log(
            "  Chat model: probing each context for minimum gpu_memory_utilization"
        )
        results = {}
        gpu_mem_util: float | None = None
        for ctx in contexts_to_measure:
            emit_log(f"  Context {ctx}:")
            try:
                util, profile = await find_min_gpu_utilization(
                    model_path,
                    ctx,
                    quantization,
                    tracker,
                    emit_log,
                    loader_config=loader_config,
                    device_index=request.gpu_index,
                    util_cap=0.95,
                )
                profile["gpu_memory_utilization"] = util
                results[str(ctx)] = profile
                if ctx == contexts_to_measure[0]:
                    gpu_mem_util = util
            except RuntimeError as e:
                emit_log(f"  ❌ {ctx}: {e}")
                results[str(ctx)] = {"error": str(e)}
        if gpu_mem_util is None:
            logger.warning(
                "All GPU measurements failed for '%s'; "
                "falling back to gpu_memory_utilization=0.95",
                request.model_id,
            )
            gpu_mem_util = 0.95

    loader_updates: dict[str, Any] = {
        "gpu_memory_utilization": gpu_mem_util,
        "enforce_eager": True,
    }
    if is_embedding:
        loader_updates["embedding"] = True
        loader_updates["embedding_task_default"] = resolve_embedding_task_default(
            request.model_id, loader_config
        )
    return results, loader_updates
