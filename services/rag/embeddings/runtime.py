"""Shared mutable runtime state for the embedding HTTP client."""

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
    """Set the embedding model ID from config."""
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
    """Inject the shared EventBus for embedding telemetry."""
    global _event_bus
    if _event_bus is not None and _event_bus is not bus:
        raise RuntimeError(
            "Embedding event bus already initialised with a different instance"
        )
    _event_bus = bus


def require_configured() -> str:
    """Return configured model id or raise if unset."""
    if not _embed_model:
        raise RuntimeError(
            "Embedding module not configured — call configure(model_id) at startup"
        )
    return _embed_model


def get_model_id() -> str:
    """Return the currently configured embedding model ID."""
    return require_configured()


async def close() -> None:
    """Close the shared HTTP client during service shutdown."""
    await _client.aclose()


def get_client() -> httpx.AsyncClient:
    return _client


def get_event_bus() -> EventBus | None:
    return _event_bus


def get_probe_payload() -> dict[str, str | list[str]]:
    require_configured()
    return _probe_payload


def get_embed_dim() -> int | None:
    return _embed_dim


def cache_embed_dim(embeddings: list[list[float]]) -> None:
    """Write-once cache of embedding dimension from first successful response."""
    global _embed_dim
    if _embed_dim is None and embeddings:
        _embed_dim = len(embeddings[0])
