"""Shared mutable runtime state for the embedding HTTP client.

Process-wide singletons: one ``httpx.AsyncClient``, the configured model ID
and probe payload, the injected event bus, and a write-once embedding
dimension. ``rag_service/lifecycle.py`` calls ``configure`` and
``set_event_bus`` at startup and ``close`` on shutdown; sibling modules read
state through the getters so all embedding calls share one client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)

_client = httpx.AsyncClient(timeout=120.0)
_embed_model: str = ""
_probe_payload: dict[str, str | list[str]] = {}
_event_bus: EventBus | None = None
_embed_dim: int | None = None


def configure(model_id: str) -> None:
    """Record the embedding model ID and build the probe payload at startup.

    Called from RAG lifecycle startup with the configured embedding model. Logs
    the parsed context-size suffix when present.

    Raises:
        ValueError: When ``model_id`` is empty or whitespace.
    """
    global _embed_model, _probe_payload
    if not model_id or not model_id.strip():
        raise ValueError(f"configure() received blank model_id: {model_id!r}")
    _embed_model = model_id
    _probe_payload = {"model": _embed_model, "input": ["probe"]}

    from services.rag.embeddings.model_id import extract_context_suffix

    ctx = extract_context_suffix(_embed_model)
    if ctx is not None:
        logger.info(
            "Embedding model configured: %s (context=%d). "
            "Verify this matches activated_gpu_contexts in the catalog entry.",
            _embed_model,
            ctx,
        )
    else:
        logger.info("Embedding model configured: %s", _embed_model)


def set_event_bus(bus: EventBus) -> None:
    """Inject the shared EventBus used for embedding query/fallback telemetry.

    Idempotent for the same bus instance.

    Raises:
        RuntimeError: When a different bus instance was already injected.
    """
    global _event_bus
    if _event_bus is not None and _event_bus is not bus:
        raise RuntimeError(
            "Embedding event bus already initialised with a different instance"
        )
    _event_bus = bus


def require_configured() -> str:
    """Return the configured embedding model ID, guarding against missing setup.

    Raises:
        RuntimeError: When ``configure(model_id)`` has not been called yet.
    """
    if not _embed_model:
        raise RuntimeError(
            "Embedding module not configured — call configure(model_id) at startup"
        )
    return _embed_model


def get_model_id() -> str:
    """Expose the configured embedding model ID to callers outside the package.

    Used by ``rag_service/api.py``; raises RuntimeError when not configured.
    """
    return require_configured()


async def close() -> None:
    """Close the process-wide embedding ``httpx.AsyncClient`` at RAG shutdown.

    Called from lifecycle teardown; the client cannot be reused afterwards.
    """
    await _client.aclose()


def get_client() -> httpx.AsyncClient:
    """Return the shared embedding ``httpx.AsyncClient`` used for all gateway POSTs."""
    return _client


def get_event_bus() -> EventBus | None:
    """Return the injected event bus for embedding telemetry, or None if unset."""
    return _event_bus


def get_probe_payload() -> dict[str, str | list[str]]:
    """Return the one-word ``probe`` embeddings request body for health checks.

    Used for dimension seeding and rewarm probes; raises RuntimeError when the
    module has not been configured.
    """
    require_configured()
    return _probe_payload


def get_embed_dim() -> int | None:
    """Return the cached embedding vector width, or None before any success.

    Zero-vector fallbacks in ``batch_post`` depend on this being known.
    """
    return _embed_dim


def cache_embed_dim(embeddings: list[list[float]]) -> None:
    """Remember the vector width from the first successful embeddings response.

    Write-once: later calls are no-ops, and empty inputs are ignored. The
    cached width sizes zero-vector fallbacks when an embedding fails.
    """
    global _embed_dim
    if _embed_dim is None and embeddings:
        _embed_dim = len(embeddings[0])
