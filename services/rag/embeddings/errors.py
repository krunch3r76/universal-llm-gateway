"""Exception types raised by the RAG embedding client package.

``EmbeddingDependencyUnavailableError`` is raised by ``health.wait_until_healthy``
and caught in ``rag_service/dependency_activation.py``;
``EmbeddingTransientError`` is raised by ``query_embed`` and caught by the RAG
search and API paths. ``_BatchRetryError`` stays internal to ``batch_post``.
"""

from __future__ import annotations


class EmbeddingDependencyUnavailableError(RuntimeError):
    """Raised when the configured embedding model cannot serve RAG at startup.

    Covers a model that is structurally absent from the Stargate catalog, and
    a dimension-seeding probe POST that fails after the model was admitted.
    Dependency activation catches it and publishes ``rag_embeddings_unavailable``.
    """


class _BatchRetryError(Exception):
    """Internal batch_post retry/split signal (not caller-facing; distinct from EmbeddingTransientError)."""


class EmbeddingTransientError(Exception):
    """Raised when search-time query embedding exhausts its transient retries.

    Thrown by ``embed_query`` and ``embed_queries_batch`` after 429/502/503/504
    or connection/timeout failures. Carries ``model_id``, ``attempts`` and
    ``last_status`` (HTTP code or None) so search callers can report or degrade.
    """

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        attempts: int,
        last_status: int | None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.attempts = attempts
        self.last_status = last_status
