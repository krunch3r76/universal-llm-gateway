"""
Embedding operations for deduplication.

Fetches embeddings via ProxyClient (routes through Stargate with orchestrator tracking).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import numpy as np
from src.scheduling.events import (
    PipelineStepEmbeddingCompleted,
    PipelineStepEmbeddingFailed,
    PipelineStepEmbeddingStarted,
)
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext

logger = get_logger(__name__)


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embeddings for cosine similarity."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return embeddings / norms


def _resolve_embedding_model_alias(model_id: str, context: PipelineContext) -> str:
    """
    Resolve embedding model alias to full ID via registry.

    Resolution order: domain-specific -> root -> passthrough

    Args:
        model_id: Alias (e.g., "embedding") or full ID
        context: Pipeline context with registry

    Returns:
        Full model ID

    Raises:
        KeyError: If alias not found and doesn't appear to be full ID
    """
    registry = context._registry
    domain = context.pipeline.domain

    try:
        model_config = registry.get_model_config(
            model_id,
            domain=domain,
            search_path=context.pipeline.source_search_path,
        )
        return model_config.model
    except KeyError:
        logger.warning(
            f"Embedding model '{model_id}' not found in registry "
            f"(domain={domain}). Passing through as full ID."
        )
        return model_id


async def get_embeddings(
    statements: list[str],
    model: str,
    context: PipelineContext,
    *,
    step_id: str = "deduplicate",
) -> np.ndarray:
    """
    Get embeddings via ProxyClient.

    Uses Stargate's orchestrator for capacity-aware routing.
    Returns L2-normalized embeddings for cosine similarity.
    Emits step events for observability (fire-and-forget).

    Model aliases (e.g., "embedding") are automatically resolved to full IDs
    via the pipeline registry before making the embedding request.

    Args:
        statements: Texts to embed
        model: Embedding model identifier (alias or full ID)
        context: Pipeline context (provides ProxyClient, _proxy.event_bus)
        step_id: Step identifier for tracing

    Returns:
        L2-normalized embeddings array of shape (n_statements, embedding_dim)

    Raises:
        ProxyClientError: On embedding request failure
        KeyError: If model alias cannot be resolved
        Exception: Re-raises any unexpected errors after emitting failed event
    """
    from systems.pipeline.core.execution.proxy_client import ProxyClientError

    resolved_model = _resolve_embedding_model_alias(model, context)
    if resolved_model != model:
        logger.debug(f"Resolved embedding model alias: {model} -> {resolved_model}")

    proxy_client = context.get_proxy_client()
    proxy = getattr(context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    execution_id = context.execution_id
    input_count = len(statements)
    start_time = time.perf_counter()

    if event_bus:
        asyncio.create_task(
            event_bus.publish_async_nowait(
                PipelineStepEmbeddingStarted(
                    execution_id=execution_id,
                    step_id=step_id,
                    model_id=resolved_model,
                    input_count=input_count,
                )
            )
        )

    try:
        response = await proxy_client.embeddings(
            model=resolved_model,
            texts=statements,
            execution_id=execution_id,
            step_id=step_id,
        )

        embeddings = np.array([d["embedding"] for d in response["data"]])
        normalized = _normalize_embeddings(embeddings)

        if event_bus:
            duration_ms = (time.perf_counter() - start_time) * 1000
            embedding_dim = embeddings.shape[1] if embeddings.ndim > 1 else 0

            asyncio.create_task(
                event_bus.publish_async_nowait(
                    PipelineStepEmbeddingCompleted(
                        execution_id=execution_id,
                        step_id=step_id,
                        model_id=resolved_model,
                        input_count=input_count,
                        duration_ms=duration_ms,
                        embedding_dim=embedding_dim,
                    )
                )
            )

        return normalized

    except ProxyClientError as e:
        if event_bus:
            duration_ms = (time.perf_counter() - start_time) * 1000
            asyncio.create_task(
                event_bus.publish_async_nowait(
                    PipelineStepEmbeddingFailed(
                        execution_id=execution_id,
                        step_id=step_id,
                        model_id=resolved_model,
                        input_count=input_count,
                        duration_ms=duration_ms,
                        error=str(e),
                        status_code=e.status_code,
                    )
                )
            )
        raise

    except Exception as e:
        if event_bus:
            duration_ms = (time.perf_counter() - start_time) * 1000
            asyncio.create_task(
                event_bus.publish_async_nowait(
                    PipelineStepEmbeddingFailed(
                        execution_id=execution_id,
                        step_id=step_id,
                        model_id=resolved_model,
                        input_count=input_count,
                        duration_ms=duration_ms,
                        error=str(e),
                        status_code=None,
                    )
                )
            )
        raise
