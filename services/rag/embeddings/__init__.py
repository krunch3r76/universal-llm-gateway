"""Embedding model client for RAG indexing and search."""

from __future__ import annotations

from services.rag.embeddings.chunk_embed import embed_chunks
from services.rag.embeddings.errors import (
    EmbeddingDependencyUnavailableError,
    EmbeddingTransientError,
)
from services.rag.embeddings.health import require_healthy, wait_until_healthy
from services.rag.embeddings.query_embed import embed_queries_batch, embed_query
from services.rag.embeddings.runtime import close, configure, get_model_id, set_event_bus

__all__ = [
    "EmbeddingDependencyUnavailableError",
    "EmbeddingTransientError",
    "close",
    "configure",
    "embed_chunks",
    "embed_queries_batch",
    "embed_query",
    "get_model_id",
    "require_healthy",
    "set_event_bus",
    "wait_until_healthy",
]
