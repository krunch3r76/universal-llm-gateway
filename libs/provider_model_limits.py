"""Provider-specific output token ceilings used for request validation/clamping."""

from __future__ import annotations

from typing import Final

# Ordered most-specific first so dated snapshots win before family aliases.
_ANTHROPIC_MAX_OUTPUT_TOKENS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4-6", 128000),
    ("claude-opus-4.6", 128000),
    ("claude-sonnet-4-6", 64000),
    ("claude-sonnet-4.6", 64000),
    ("claude-haiku-4-5-20251001", 64000),
    ("claude-haiku-4-5", 64000),
    ("claude-haiku-4.5", 64000),
    ("claude-opus-4-5-20251101", 64000),
    ("claude-opus-4-5", 64000),
    ("claude-opus-4.5", 64000),
    ("claude-sonnet-4-5-20250929", 64000),
    ("claude-sonnet-4-5", 64000),
    ("claude-sonnet-4.5", 64000),
    ("claude-opus-4-1-20250805", 32000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4.1", 32000),
    ("claude-sonnet-4-20250514", 64000),
    ("claude-sonnet-4-0", 64000),
    ("claude-sonnet-4", 64000),
    ("claude-opus-4-20250514", 32000),
    ("claude-opus-4-0", 32000),
    ("claude-opus-4", 32000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-opus", 4096),
    ("claude-3-sonnet", 4096),
    ("claude-3-haiku", 4096),
)

_UNKNOWN_ANTHROPIC_MAX_OUTPUT_TOKENS: Final[int] = 8192


def anthropic_max_output_tokens(model: str) -> int:
    """Return the best-known Anthropic max output tokens for ``model``.

    Anthropic rejects ``max_tokens`` values above the per-model ceiling. When a
    model is unknown to our static table, return a conservative fallback so we
    fail closed instead of sending obviously invalid oversized requests.
    """
    normalized = str(model).strip().lower()
    for marker, limit in _ANTHROPIC_MAX_OUTPUT_TOKENS:
        if marker in normalized:
            return limit
    return _UNKNOWN_ANTHROPIC_MAX_OUTPUT_TOKENS


def clamp_anthropic_max_tokens(model: str, requested_max_tokens: int | None) -> int:
    """Clamp a requested Anthropic ``max_tokens`` value to the model ceiling.

    When ``requested_max_tokens`` is ``None`` (caller did not specify), returns
    the model's full output ceiling — ¬conservative default.
    """
    ceiling = anthropic_max_output_tokens(model)
    if requested_max_tokens is None:
        return ceiling
    return min(requested_max_tokens, ceiling)


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
