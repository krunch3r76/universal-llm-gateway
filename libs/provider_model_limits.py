"""Local-model inference/load timeout budgets used for RAG pipeline deadlines.

The Anthropic max-output ceiling table + clamp helpers were deleted in the
per-model ModelDispatchSpec build (thread 1234/1271): cloud max-output now
resolves through the typed ``llm_adapters.capability_dispatch`` registry at the
single frontier boundary, not a libs-resident static table. This module retains
only the local-runtime inference-timeout half.
"""

from __future__ import annotations

from typing import Final

# p99 inference time (seconds) for a typical RAG-scale task: reranking ~14 candidate
# chunks or generating ~1000 tokens under single-request, uncontested GPU load.
_LOCAL_MODEL_INFERENCE_TIMEOUT_S: tuple[tuple[str, float], ...] = (
    ("cross_encoder", 20.0),
    ("qwen3_4b", 40.0),
    ("qwen3_9b", 60.0),
    ("qwen3_14b", 90.0),
    ("phi4", 90.0),
)

_UNKNOWN_LOCAL_INFERENCE_TIMEOUT_S: Final[float] = 90.0

# Budget for on-demand model loading when the model is not resident in VRAM.
# Covers p99 load time including VRAM allocation and engine warm-up.
# ∀ pipeline timeout: if model is already resident, this budget is unused.
_MODEL_LOAD_BUDGET_S: Final[float] = 180.0


def local_model_inference_timeout(model: str) -> float:
    """Return the p99 inference timeout (seconds) for a typical RAG-scale task.

    Matches on substring so "qwen3_9b-instruct" resolves the same as "qwen3_9b".
    Returns ``_UNKNOWN_LOCAL_INFERENCE_TIMEOUT_S`` for unrecognised models.
    """
    normalized = str(model).strip().lower()
    for marker, limit in _LOCAL_MODEL_INFERENCE_TIMEOUT_S:
        if marker in normalized:
            return limit
    return _UNKNOWN_LOCAL_INFERENCE_TIMEOUT_S


def rag_pipeline_timeout(rerank_model: str) -> float:
    """Adaptive pipeline timeout: model load budget + per-model inference time.

    Ensures the timeout is never exhausted during model loading — the load budget
    covers p99 VRAM load time. When the model is already resident, the load budget
    is not consumed and the deadline is effectively just the inference portion.

    ∀ rerank_model: timeout = _MODEL_LOAD_BUDGET_S + local_model_inference_timeout(rerank_model)
    """
    return _MODEL_LOAD_BUDGET_S + local_model_inference_timeout(rerank_model)
