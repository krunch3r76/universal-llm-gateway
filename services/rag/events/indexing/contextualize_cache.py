"""RAG indexing event factories — contextualize cache read/write/gc."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_contextualize_cache_evaluated(
    *,
    file: str,
    total_chunks: int,
    cache_hits: int,
    cache_misses: int,
    contextualize_model: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted once per file after cache planning decides which chunks reuse vs recompute."""
    payload: dict[str, object] = {
        "file": file,
        "total_chunks": total_chunks,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "contextualize_model": contextualize_model,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualize.cache.evaluated",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualize_cache_lookup_failed(
    *,
    file: str,
    requested_chunks: int,
    contextualize_model: str,
    error: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when cache lookup failed and indexing degraded to full recompute."""
    payload: dict[str, object] = {
        "file": file,
        "requested_chunks": requested_chunks,
        "contextualize_model": contextualize_model,
        "error": error,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualize.cache.lookup.failed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualize_cache_store_completed(
    *,
    file: str,
    stored: int,
    requested: int,
    contextualize_model: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted after cache rows persist following successful upsert + source commit."""
    payload: dict[str, object] = {
        "file": file,
        "stored": stored,
        "requested": requested,
        "contextualize_model": contextualize_model,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualize.cache.store.completed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualize_cache_store_failed(
    *,
    file: str,
    requested: int,
    contextualize_model: str,
    error: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when cache persistence fails but indexing itself already succeeded."""
    payload: dict[str, object] = {
        "file": file,
        "requested": requested,
        "contextualize_model": contextualize_model,
        "error": error,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualize.cache.store.failed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualize_cache_gc_completed(*, deleted_rows: int) -> Event:
    """Emitted after the startup orphan sweep for the contextualize cache."""
    return Event(
        signal="rag.contextualize.cache.gc.completed",
        role="observation",
        payload={"deleted_rows": deleted_rows},
    )


@event_factory
def rag_contextualize_cache_gc_failed(*, error: str) -> Event:
    """Emitted when the startup orphan sweep for the contextualize cache fails non-fatally."""
    return Event(
        signal="rag.contextualize.cache.gc.failed",
        role="observation",
        payload={"error": error},
    )
