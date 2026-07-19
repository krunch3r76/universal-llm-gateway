"""Gateway POST batching with retry, split-on-500, and zero-vector fallback."""

from __future__ import annotations

import asyncio
import random
import time

import httpx
from universal_logging import get_logger

from services.rag.embeddings.constants import (
    EMBED_RETRY_ATTEMPTS,
    EMBED_RETRY_BACKOFF_S,
    GATEWAY_URL,
    REWARM_POLL_INTERVAL_S,
    REWARM_PROBE_TIMEOUT_S,
    REWARM_TIMEOUT_S,
    TRANSIENT_STATUS_CODES,
)
from services.rag.embeddings.errors import TransientEmbeddingError
from services.rag.embeddings.model_id import max_batch_tokens_for_model
from services.rag.embeddings.runtime import (
    cache_embed_dim,
    get_client,
    get_embed_dim,
    get_event_bus,
    get_probe_payload,
    require_configured,
)

logger = get_logger(__name__)

__all__ = ["post_embeddings"]


def parse_embedding_rows(payload: dict[str, object]) -> list[list[float]]:
    """Extract embedding vectors from gateway response payload."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Embedding response missing list 'data' field")
    vectors: list[list[float]] = []
    for row in data:
        if not isinstance(row, dict):
            raise RuntimeError("Embedding response item is not an object")
        embedding = row.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Embedding response item missing 'embedding' list")
        vectors.append(embedding)
    return vectors


async def _handle_single_item_500(text: str, error_body: str) -> list[list[float]]:
    """Recover from a 500 on a single-item embedding batch."""
    from services.rag.embeddings.constants import CHARS_PER_TOKEN

    model_id = require_configured()
    client = get_client()
    max_chars = max_batch_tokens_for_model(model_id) * CHARS_PER_TOKEN

    if len(text) > max_chars:
        truncated = text[:max_chars]
        logger.warning(
            "Truncating oversized text from %d to %d chars for embedding retry "
            "(model=%s)",
            len(text),
            max_chars,
            model_id,
        )
        try:
            response = await client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": model_id, "input": [truncated]},
            )
            if response.status_code == 200:
                result = parse_embedding_rows(response.json())
                cache_embed_dim(result)
                return result
            logger.error(
                "Truncated text retry returned non-200 status %d "
                "(model=%s, truncated_len=%d)",
                response.status_code,
                model_id,
                max_chars,
            )
        except Exception as retry_exc:
            logger.error(
                "Truncated text still failed embedding (model=%s, truncated_len=%d): %s",
                model_id,
                max_chars,
                retry_exc,
            )

        embed_dim = get_embed_dim()
        if embed_dim is not None:
            logger.warning(
                "Substituting zero vector (dim=%d) for failed embedding", embed_dim
            )
            return [[0.0] * embed_dim]

        raise RuntimeError(
            f"Single-item embedding failed and embedding dimension unknown "
            f"(model={model_id}, text_len={len(text)})"
        )

    logger.warning(
        "Single text within limits (len=%d <= %d chars) failed with 500 "
        "(model=%s) — transient error (VRAM pressure or model fault); retrying. Error: %s",
        len(text),
        max_chars,
        model_id,
        error_body,
    )
    raise TransientEmbeddingError(
        f"Embedding 500 on within-limits text (model={model_id}, text_len={len(text)})"
    )


def _fallback_to_zero_vector(text_len: int) -> list[list[float]] | None:
    """Return a zero vector after all retry attempts for a single-item batch."""
    from services.rag.events.indexing import rag_embedding_chunk_fallback

    embed_dim = get_embed_dim()
    model_id = require_configured()
    if embed_dim is None:
        return None
    logger.warning(
        "Single-item embedding failed after %d attempts (model=%s, text_len=%d) — "
        "content-specific fault; substituting zero vector (dim=%d)",
        EMBED_RETRY_ATTEMPTS,
        model_id,
        text_len,
        embed_dim,
    )
    event_bus = get_event_bus()
    if event_bus is not None:
        asyncio.create_task(
            event_bus.publish_nowait(
                rag_embedding_chunk_fallback(
                    model=model_id,
                    text_len=text_len,
                    dim=embed_dim,
                )
            )
        )
    return [[0.0] * embed_dim]


async def _wait_for_model_ready(timeout_s: float = REWARM_TIMEOUT_S) -> bool:
    """Wait for the embedding model to respond to a probe after eviction."""
    model_id = require_configured()
    client = get_client()
    probe_payload = get_probe_payload()
    deadline = time.monotonic() + timeout_s
    probe = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Embedding model rewarm timed out after %.0fs (model=%s)",
                timeout_s,
                model_id,
            )
            return False
        probe += 1
        probe_timeout = min(REWARM_PROBE_TIMEOUT_S, remaining)
        try:
            response = await client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json=probe_payload,
                timeout=probe_timeout,
            )
            if response.status_code == 200:
                logger.info(
                    "Embedding model ready after %d rewarm probe(s) (model=%s)",
                    probe,
                    model_id,
                )
                return True
            if response.status_code not in TRANSIENT_STATUS_CODES:
                logger.warning(
                    "Rewarm probe returned non-transient %d (model=%s); stopping",
                    response.status_code,
                    model_id,
                )
                return False
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        wait = min(REWARM_POLL_INTERVAL_S, deadline - time.monotonic())
        if wait > 0:
            logger.debug(
                "Rewarm probe %d got transient response; retrying in %.0fs (model=%s)",
                probe,
                wait,
                model_id,
            )
            await asyncio.sleep(wait)


async def post_embeddings(batch: list[str]) -> list[list[float]]:
    """POST a single batch to the embedding endpoint with retry and fallback."""
    model_id = require_configured()
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(EMBED_RETRY_ATTEMPTS):
        try:
            response = await client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": model_id, "input": batch},
            )
            if response.status_code == 500:
                body = response.text
                if len(batch) == 1:
                    return await _handle_single_item_500(batch[0], body)
                mid = len(batch) // 2
                logger.warning(
                    "Embedding 500 on batch of %d texts (model=%s); "
                    "splitting at midpoint %d for recovery. Error: %s",
                    len(batch),
                    model_id,
                    mid,
                    body,
                )
                left = await post_embeddings(batch[:mid])
                right = await post_embeddings(batch[mid:])
                return left + right
            response.raise_for_status()
            result = parse_embedding_rows(response.json())
            cache_embed_dim(result)
            return result
        except TransientEmbeddingError as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in TRANSIENT_STATUS_CODES:
                raise
            last_exc = exc
            if exc.response.status_code in {503, 504}:
                logger.warning(
                    "Embedding got %d (model=%s, batch_size=%d, attempt %d/%d); "
                    "waiting for model to reload before retry",
                    exc.response.status_code,
                    model_id,
                    len(batch),
                    attempt + 1,
                    EMBED_RETRY_ATTEMPTS,
                )
                await _wait_for_model_ready()
                continue
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
        base_delay = EMBED_RETRY_BACKOFF_S * (2**attempt)
        delay = base_delay * random.uniform(0.75, 1.25)
        logger.warning(
            "Embedding request failed (attempt %d/%d, %s); retrying in %.1fs",
            attempt + 1,
            EMBED_RETRY_ATTEMPTS,
            type(last_exc).__name__,
            delay,
        )
        await asyncio.sleep(delay)
    if isinstance(last_exc, TransientEmbeddingError) and len(batch) == 1:
        fallback = _fallback_to_zero_vector(len(batch[0]))
        if fallback is not None:
            return fallback
        logger.error(
            "Single-item embedding failed after %d attempts (model=%s, "
            "text_len=%d) — embedding dimension unknown, cannot produce "
            "zero-vector fallback",
            EMBED_RETRY_ATTEMPTS,
            model_id,
            len(batch[0]),
        )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Embedding request failed without capturing an exception")
