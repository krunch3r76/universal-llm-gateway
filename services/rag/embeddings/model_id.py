"""Model ID parsing and batch token limits for embedding requests."""

from __future__ import annotations

import re

from universal_logging import get_logger

from services.rag.embeddings.constants import FALLBACK_MAX_BATCH_TOKENS, N_CTX_HEADROOM

logger = get_logger(__name__)

_CONTEXT_SUFFIX_RE = re.compile(r"-(\d+)(?:-(?:cpu|hybrid))?$")


def extract_context_suffix(model_id: str) -> int | None:
    """Parse trailing context-size suffix from a synthetic model ID."""
    match = _CONTEXT_SUFFIX_RE.search(model_id)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        logger.warning(
            "Failed to parse context suffix as integer from model_id: %s", model_id
        )
        return None


def max_batch_tokens_for_model(model_id: str) -> int:
    """Derive per-batch token cap from the model's context-size suffix."""
    ctx = extract_context_suffix(model_id)
    if ctx is not None:
        return int(ctx * N_CTX_HEADROOM)
    return FALLBACK_MAX_BATCH_TOKENS


def is_instruction_aware_model(model_id: str) -> bool:
    """Detect whether the embedding model supports Instruct:/Query: format."""
    return "qwen3-embedding" in model_id.lower()
