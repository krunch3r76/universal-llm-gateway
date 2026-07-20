"""GGUF embedding step-down VRAM measurement for llama-cpp loader models."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.events import get_event_bus
from ...core.events.measurement import MeasurementEmbeddingDetected
from ..context_detection import get_embedding_contexts
from .context_selection import resolve_embedding_task_default
from .execution import SubprocessTracker
from .gpu import run_layer_test
from .request import MeasureJobRequest


async def measure_gguf_embedding_profiles(
    request: MeasureJobRequest,
    model_path: Path,
    tracker: SubprocessTracker,
    entry: dict[str, Any] | None,
    emit_log: Callable[[str], None],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Step-down GGUF embedding measurement.

    ∀ llama-cpp embedding model: KV cache scales with n_ctx even in
    embedding mode. Step down from training_context_length so each
    profile's VRAM/RAM reflects the actual cost at that context size.
    """
    loader_config = (entry or {}).get("loader", {})

    training_ctx = request.training_context_length
    contexts = request.contexts or (
        get_embedding_contexts(training_ctx) if training_ctx else None
    )
    if not contexts:
        raise RuntimeError(
            f"Embedding model '{request.model_id}' has no "
            "training_context_length; cannot determine contexts to probe."
        )

    n_layers = 0 if request.mode == "cpu" else -1
    mode_label = "CPU" if request.mode == "cpu" else "GPU"

    emit_log(f"  Engine: llama-cpp (embedding {mode_label}, contexts: {contexts})")
    await get_event_bus().publish_nowait(
        MeasurementEmbeddingDetected(
            model_id=request.model_id, context_length=contexts[0]
        )
    )

    pooling = loader_config.get("pooling")
    ubatch_size = loader_config.get("ubatch_size")

    results: dict[str, dict[str, Any]] = {}
    for ctx in contexts:
        emit_log(f"  Probing context {ctx}...")
        profile = await run_layer_test(
            model_path,
            n_layers=n_layers,
            context=ctx,
            n_batch=ctx,
            gpu_index=request.gpu_index,
            mmproj_path=None,
            tracker=tracker,
            embedding=True,
            pooling=pooling,
            ubatch_size=ubatch_size,
        )

        if profile.get("success"):
            profile["n_gpu_layers"] = n_layers
            vram = profile.get("vram_mb", "N/A")
            ram = profile.get("ram_mb", "N/A")
            emit_log(f"  ✅ {ctx}: VRAM={vram}MB, RAM={ram}MB")
            results[str(ctx)] = profile
        else:
            error = profile.get("error", "unknown")
            emit_log(f"  ❌ {ctx}: {error}")
            results[str(ctx)] = {"error": error}

    loader_updates: dict[str, Any] = {
        "embedding": True,
        "embedding_task_default": resolve_embedding_task_default(
            request.model_id, loader_config
        ),
    }
    if pooling is not None:
        loader_updates["pooling"] = pooling
    if ubatch_size is not None:
        loader_updates["ubatch_size"] = ubatch_size
    return results, loader_updates
