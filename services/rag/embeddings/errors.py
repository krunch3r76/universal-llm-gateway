"""Embedding client exception types."""

from __future__ import annotations


class EmbeddingDependencyUnavailableError(RuntimeError):
    """Embedding unavailable: not in catalog or dim seed failed after admission."""


class TransientEmbeddingError(Exception):
    """Retryable embedding backend failure (VRAM pressure, transient model fault)."""


class EmbeddingTransientError(Exception):
    """Raised when embed_query retries are exhausted on transient failures."""

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
