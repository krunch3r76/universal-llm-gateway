"""Cross-encoder reranking for RAG query results.

Loads a cross-encoder model (e.g. BAAI/bge-reranker-v2-m3) and scores
(query, passage) pairs in a single forward pass. Produces relevance scores
without text generation — ~100-200ms for 14 pairs on RTX 5090.

The model loads lazily on first call and stays resident. GPU memory footprint
is ~550MB for bge-reranker-v2-m3.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder as _CrossEncoderType

logger = logging.getLogger(__name__)

_model: _CrossEncoderType | None = None
_model_name: str = ""

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


def configure(model_name: str = DEFAULT_MODEL) -> None:
    """Set the cross-encoder model name. Does NOT load the model — lazy init."""
    global _model_name
    _model_name = model_name
    logger.info("Cross-encoder configured: %s (lazy load on first call)", model_name)


def _ensure_loaded() -> _CrossEncoderType:
    """Load the cross-encoder model on first use."""
    global _model
    if _model is not None:
        return _model

    from sentence_transformers import CrossEncoder

    name = _model_name or DEFAULT_MODEL
    logger.info("Loading cross-encoder model: %s", name)
    start = time.monotonic()
    _model = CrossEncoder(name, device="cuda", trust_remote_code=True)
    elapsed = time.monotonic() - start
    logger.info("Cross-encoder loaded in %.2fs: %s", elapsed, name)
    return _model


def rerank(
    query: str,
    passages: list[str],
) -> list[float]:
    """Score (query, passage) pairs and return relevance scores.

    Returns a list of float scores, one per passage, in the same order
    as the input passages. Higher score = more relevant.
    """
    if not passages:
        return []
    model = _ensure_loaded()
    pairs = [(query, p) for p in passages]
    scores = model.predict(pairs)
    return [float(s) for s in scores]


def is_available() -> bool:
    """Check whether the cross-encoder module is configured and importable."""
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401

        return bool(_model_name or DEFAULT_MODEL)
    except ImportError:
        return False
