"""Index-time chunk embedding with token-aware batching."""

from __future__ import annotations

from universal_logging import get_logger

from services.rag.embeddings.batch_post import post_embeddings
from services.rag.embeddings.constants import CHARS_PER_TOKEN, EMBED_BATCH_SIZE
from services.rag.embeddings.model_id import max_batch_tokens_for_model
from services.rag.embeddings.runtime import require_configured

logger = get_logger(__name__)

__all__ = ["embed_chunks"]


async def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed raw texts for indexing with count- and token-bounded sub-batches."""
    model_id = require_configured()
    max_batch_tokens = max_batch_tokens_for_model(model_id)
    all_embeddings: list[list[float]] = []
    batch: list[str] = []
    batch_tokens = 0

    for text in texts:
        token_estimate = max(1, len(text) // CHARS_PER_TOKEN)
        if token_estimate > max_batch_tokens:
            if batch:
                all_embeddings.extend(await post_embeddings(batch))
                batch = []
                batch_tokens = 0
            logger.warning(
                "Single text estimate exceeds batch cap (tokens=%d > cap=%d, model=%s); "
                "sending as single-item batch for truncation/fallback handling",
                token_estimate,
                max_batch_tokens,
                model_id,
            )
            all_embeddings.extend(await post_embeddings([text]))
            continue
        if batch and (
            len(batch) >= EMBED_BATCH_SIZE
            or batch_tokens + token_estimate > max_batch_tokens
        ):
            all_embeddings.extend(await post_embeddings(batch))
            batch = []
            batch_tokens = 0
        batch.append(text)
        batch_tokens += token_estimate

    if batch:
        all_embeddings.extend(await post_embeddings(batch))

    return all_embeddings
