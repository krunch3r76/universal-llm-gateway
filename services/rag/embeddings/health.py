"""Startup health gating for the configured embedding model."""

from __future__ import annotations

import httpx
from universal_logging import get_logger

from services.rag.embeddings.constants import GATEWAY_URL, PROBE_TIMEOUT_S
from services.rag.embeddings.errors import EmbeddingDependencyUnavailableError
from services.rag.embeddings.runtime import (
    cache_embed_dim,
    get_client,
    get_embed_dim,
    get_probe_payload,
    require_configured,
)
from services.rag.model_availability_tracker import get_model_availability_tracker

logger = get_logger(__name__)

__all__ = ["require_healthy", "wait_until_healthy"]


async def wait_until_healthy(
    timeout_s: float = PROBE_TIMEOUT_S,
    interval_s: float = 2.0,
) -> None:
    """Wait for aggregate embedding admission, then seed the cached embedding dimension."""
    del interval_s
    model_id = require_configured()
    tracker = get_model_availability_tracker()
    if tracker is None:
        raise RuntimeError(
            "ModelAvailabilityTracker not initialized — lifecycle must configure it before wait_until_healthy()"
        )
    result = await tracker.wait_until_available(model_id, timeout_s)
    if not result.available:
        if result.reason.is_structural:
            raise EmbeddingDependencyUnavailableError(
                f"Embedding model {model_id!r} not in catalog: {result.detail}"
            )
        raise TimeoutError(
            f"Embedding model {model_id!r} not aggregate-available after {timeout_s}s"
            f" ({result.reason.value})"
        )
    client = get_client()
    probe_payload = get_probe_payload()
    try:
        response = await client.post(
            f"{GATEWAY_URL}/v1/embeddings",
            json=probe_payload,
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise EmbeddingDependencyUnavailableError(
            "Embedding dim seed failed after aggregate availability: "
            f"HTTP {exc.response.status_code} {exc.response.text!r}"
        ) from exc
    except httpx.RequestError as exc:
        raise EmbeddingDependencyUnavailableError(
            f"Embedding dim seed failed after aggregate availability: {exc!r}"
        ) from exc
    data = response.json().get("data", [])
    if data:
        cache_embed_dim([item["embedding"] for item in data])
        logger.info(
            "Embedding dim seeded after availability (dim=%s, model=%s)",
            get_embed_dim(),
            model_id,
        )
    else:
        logger.info("Embedding POST ok after availability (model=%s)", model_id)


async def require_healthy(timeout_s: float = 120.0) -> None:
    """Gate indexing until aggregate routing admits the embedding model again."""
    model_id = require_configured()
    tracker = get_model_availability_tracker()
    if tracker is None:
        raise RuntimeError("ModelAvailabilityTracker not initialized")
    if tracker.is_available(model_id):
        return
    logger.info(
        "Embedding model '%s' aggregate-unavailable — waiting up to %.0fs",
        model_id,
        timeout_s,
    )
    result = await tracker.wait_until_available(model_id, timeout_s)
    if not result.available:
        raise RuntimeError(
            f"Embedding model '{model_id}' unavailable within {timeout_s:.0f}s"
            f" ({result.reason.value}: {result.detail})."
        )
