"""Search-time query embedding with instruction prefixes and bounded retry."""

from __future__ import annotations

import asyncio
import random

import httpx
from universal_logging import get_logger

from services.rag.embeddings.batch_post import post_embeddings
from services.rag.embeddings.constants import (
    DEFAULT_INSTRUCTION,
    GATEWAY_URL,
    QUERY_RETRY_ATTEMPTS,
    QUERY_RETRY_BASE_S,
    QUERY_RETRY_MAX_S,
    SCOPE_INSTRUCTIONS,
    TRANSIENT_STATUS_CODES,
)
from services.rag.embeddings.errors import EmbeddingTransientError
from services.rag.embeddings.model_id import is_instruction_aware_model
from services.rag.embeddings.runtime import get_client, get_event_bus, require_configured
from services.rag.events.query import rag_embedding_query_success

logger = get_logger(__name__)

__all__ = ["embed_queries_batch", "embed_query"]


def format_query_text(text: str, scope: str | list[str] | None = None) -> str:
    """Apply instruction prefix for instruction-aware embedding models."""
    model_id = require_configured()
    if isinstance(scope, list):
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    if is_instruction_aware_model(model_id):
        instruction = SCOPE_INSTRUCTIONS.get(effective_scope or "", DEFAULT_INSTRUCTION)
        return f"Instruct: {instruction}\nQuery: {text}"
    return f"search_query: {text}"


def _is_transient_status(status_code: int) -> bool:
    return status_code in TRANSIENT_STATUS_CODES


async def embed_queries_batch(
    texts: list[str],
    scope: str | list[str] | None = None,
) -> list[list[float]]:
    """Embed multiple search queries in a single batch forward pass."""
    model_id = require_configured()
    if not texts:
        return []
    formatted = [format_query_text(t, scope) for t in texts]
    try:
        embeddings = await post_embeddings(formatted)
    except Exception:
        logger.error(
            "embed_queries_batch failed for %d texts (model=%s)",
            len(texts),
            model_id,
            exc_info=True,
        )
        raise EmbeddingTransientError(
            f"Batch query embedding failed for {len(texts)} texts (model={model_id})",
            model_id=model_id,
            attempts=3,
            last_status=None,
        )
    event_bus = get_event_bus()
    if event_bus is not None:
        await event_bus.publish_nowait(
            rag_embedding_query_success(
                model_id=model_id,
                query_len=sum(len(t) for t in texts),
                scope=scope,
            )
        )
    return embeddings


async def embed_query(text: str, scope: str | list[str] | None = None) -> list[float]:
    """Embed a search query with bounded jittered backoff on transient errors."""
    model_id = require_configured()
    if isinstance(scope, list):
        if scope and len(scope) > 1:
            logger.warning(
                "embed_query received multiple scopes; using first only: %s",
                scope[0],
            )
        effective_scope = scope[0] if scope else None
    else:
        effective_scope = scope
    formatted = format_query_text(text, scope=effective_scope)
    client = get_client()

    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(1, QUERY_RETRY_ATTEMPTS + 1):
        try:
            response = await client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": model_id, "input": [formatted]},
            )
            if _is_transient_status(response.status_code):
                last_status = response.status_code
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            else:
                response.raise_for_status()
                data = response.json()
                embedding = data["data"][0]["embedding"]
                event_bus = get_event_bus()
                if event_bus is not None:
                    await event_bus.publish_nowait(
                        rag_embedding_query_success(
                            model_id=model_id,
                            query_len=len(text),
                            scope=scope,
                        )
                    )
                return embedding
        except httpx.HTTPStatusError as exc:
            if not _is_transient_status(exc.response.status_code):
                raise
            last_status = exc.response.status_code
            last_exc = exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc

        if attempt < QUERY_RETRY_ATTEMPTS:
            base_delay = QUERY_RETRY_BASE_S * (2 ** (attempt - 1))
            delay = min(base_delay, QUERY_RETRY_MAX_S) * random.uniform(0.75, 1.25)
            logger.warning(
                "embed_query failed (attempt %d/%d, %s); retrying in %.2fs (model=%s)",
                attempt,
                QUERY_RETRY_ATTEMPTS,
                type(last_exc).__name__,
                delay,
                model_id,
            )
            await asyncio.sleep(delay)

    logger.error(
        "embed_query retries exhausted (%d attempts, last_status=%s, model=%s)",
        QUERY_RETRY_ATTEMPTS,
        last_status,
        model_id,
    )
    event_bus = get_event_bus()
    if event_bus is not None:
        from services.rag.events.query import rag_embedding_query_failed

        await event_bus.publish_nowait(
            rag_embedding_query_failed(
                model_id=model_id,
                attempts=QUERY_RETRY_ATTEMPTS,
                last_status=last_status,
                query_len=len(text),
                scope=scope,
            )
        )
    raise EmbeddingTransientError(
        f"Embedding query failed after {QUERY_RETRY_ATTEMPTS} attempts "
        f"(model={model_id}, last_status={last_status})",
        model_id=model_id,
        attempts=QUERY_RETRY_ATTEMPTS,
        last_status=last_status,
    )
