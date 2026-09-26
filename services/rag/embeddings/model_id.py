"""Model ID parsing and batch token limits for embedding requests.

Synthetic model IDs may end in a context-size suffix such as ``-8192`` or
``-8192-cpu``; this module parses it to size per-batch token caps (context
times ``N_CTX_HEADROOM``, else ``FALLBACK_MAX_BATCH_TOKENS``) for
``chunk_embed`` and ``batch_post``, and flags instruction-aware models for
``query_embed`` prefixing. The ``runtime.configure`` log also uses the suffix.
"""

from __future__ import annotations

import re

from universal_logging import get_logger

from services.rag.embeddings.constants import FALLBACK_MAX_BATCH_TOKENS, N_CTX_HEADROOM

logger = get_logger(__name__)

_CONTEXT_SUFFIX_RE = re.compile(r"-(\d+)(?:-(?:cpu|hybrid))?$")


def extract_context_suffix(model_id: str) -> int | None:
    """Read the trailing ``-<digits>`` context size (optionally ``-cpu``/``-hybrid``).

    Returns:
        The context size as int, or None when the model ID has no suffix.
    """
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
    """Compute the embedding batch token budget from the model context suffix.

    Returns ``int(ctx * N_CTX_HEADROOM)`` when a context suffix is present,
    otherwise ``FALLBACK_MAX_BATCH_TOKENS``. Used for batching and truncation.
    """
    ctx = extract_context_suffix(model_id)
    if ctx is not None:
        return int(ctx * N_CTX_HEADROOM)
    return FALLBACK_MAX_BATCH_TOKENS


def is_instruction_aware_model(model_id: str) -> bool:
    """Report whether queries should use the ``Instruct:``/``Query:`` prompt format.

    True for Qwen3 embedding models (case-insensitive ``qwen3-embedding`` in
    the ID); other models get the ``search_query:`` prefix instead.
    """
    return "qwen3-embedding" in model_id.lower()
