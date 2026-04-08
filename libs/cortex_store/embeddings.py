"""Lightweight embedding client for cortex-api vector search.

Calls the Gateway's /v1/embeddings endpoint to embed assertion text for
ChromaDB storage and hybrid search. Synchronous (cortex-api routes are sync)
with retry+backoff on transient errors.

DO NOT import from services.rag.embeddings — cortex-api must be self-contained.
"""

from __future__ import annotations

import logging
import random
import time

import httpx

logger = logging.getLogger("cortex-api.embeddings")

GATEWAY_URL = "http://localhost:9999"

_embed_model: str = ""
_client: httpx.Client | None = None

_BATCH_SIZE = 16
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0
_TRANSIENT_CODES = frozenset({429, 502, 503, 504})
_REQUEST_TIMEOUT = 120.0

_INSTRUCTION = "Find relevant knowledge assertions about this topic"


def configure(model_id: str) -> None:
    """Set embedding model ID. Call once at startup."""
    global _embed_model, _client
    if not model_id or not model_id.strip():
        raise ValueError(f"configure() received blank model_id: {model_id!r}")
    _embed_model = model_id
    _client = httpx.Client(timeout=_REQUEST_TIMEOUT)
    logger.info("Cortex embedding model configured: %s", _embed_model)


def is_configured() -> bool:
    """Return True if configure() has been called with a valid model."""
    return bool(_embed_model)


def _require_configured() -> None:
    if not _embed_model:
        raise RuntimeError(
            "Embedding module not configured — call configure(model_id) at startup"
        )


def _post_batch(texts: list[str]) -> list[list[float]]:
    """POST a batch to the embedding endpoint with retry."""
    _require_configured()
    assert _client is not None

    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            response = _client.post(
                f"{GATEWAY_URL}/v1/embeddings",
                json={"model": _embed_model, "input": texts},
            )
            if response.status_code in _TRANSIENT_CODES:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            elif response.status_code == 500 and len(texts) > 1:
                mid = len(texts) // 2
                logger.warning(
                    "Embedding 500 on batch of %d; splitting at %d",
                    len(texts),
                    mid,
                )
                left = _post_batch(texts[:mid])
                right = _post_batch(texts[mid:])
                return left + right
            else:
                response.raise_for_status()
                data = response.json().get("data", [])
                return [item["embedding"] for item in data]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _TRANSIENT_CODES:
                raise
            last_exc = exc
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc

        delay = _RETRY_BACKOFF_S * (2**attempt) * random.uniform(0.75, 1.25)
        logger.warning(
            "Embedding request failed (attempt %d/%d, %s); retrying in %.1fs",
            attempt + 1,
            _RETRY_ATTEMPTS,
            type(last_exc).__name__,
            delay,
        )
        time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Embedding request failed without capturing an exception")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed texts for indexing. Splits into sub-batches of _BATCH_SIZE."""
    _require_configured()
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        all_embeddings.extend(_post_batch(batch))
    return all_embeddings


def embed_query(text: str) -> list[float]:
    """Embed a single search query with instruction prefix."""
    _require_configured()
    if "qwen3-embedding" in _embed_model.lower():
        formatted = f"Instruct: {_INSTRUCTION}\nQuery: {text}"
    else:
        formatted = f"search_query: {text}"
    result = _post_batch([formatted])
    return result[0]


def close() -> None:
    """Close the shared HTTP client during shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
