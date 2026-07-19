"""RAG indexing event factories — chunk-level contextualization signals."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_chunk_noise_tagged(
    *,
    chunk_id: str,
    source: str,
    noise_reason: str,
) -> Event:
    """Emitted for each chunk tagged ``is_noise`` at index time.

    Provides per-chunk visibility into heuristic noise classification so operators
    can audit false positives without querying ChromaDB directly.
    """
    return Event(
        signal="rag.chunk.noise.tagged",
        payload={
            "chunk_id": chunk_id,
            "source": source,
            "noise_reason": noise_reason,
        },
    )


@event_factory
def rag_chunk_contextualization_started(
    *,
    file: str,
    chunk_index: int,
    model: str,
    request_id: str,
    timeout_s: float,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when one chunk contextualization request is submitted to Stargate."""
    payload: dict[str, str | int | float] = {
        "file": file,
        "chunk_index": chunk_index,
        "model": model,
        "request_id": request_id,
        "timeout_s": timeout_s,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.chunk.contextualization.started",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_chunk_contextualization_completed(
    *,
    file: str,
    chunk_index: int,
    model: str,
    request_id: str,
    duration_seconds: float,
    output_chars: int,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when one chunk contextualization request returns successfully."""
    payload: dict[str, str | int | float] = {
        "file": file,
        "chunk_index": chunk_index,
        "model": model,
        "request_id": request_id,
        "duration_seconds": duration_seconds,
        "output_chars": output_chars,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.chunk.contextualization.completed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_chunk_contextualization_failed(
    *,
    file: str,
    chunk_index: int,
    model: str,
    error: str,
    request_id: str | None = None,
    duration_seconds: float | None = None,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted for each chunk that failed contextualization within a file.

    Per-chunk companion to rag.contextualization.partial (which aggregates
    across all failures for a file). Makes individual failed chunks queryable
    without consulting RAG logs — useful for diagnosing repeated failures on
    specific chunk positions or content patterns.
    """
    payload: dict[str, str | int] = {
        "file": file,
        "chunk_index": chunk_index,
        "model": model,
        "error": error,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.chunk.contextualization.failed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_embedding_chunk_fallback(
    *,
    model: str,
    text_len: int,
    dim: int,
) -> Event:
    """Emitted when a single-item embedding batch fails all retries and a zero vector is substituted.

    Signals a content-specific fault — the chunk is retained in the index with a
    zero vector and is not retrievable by semantic search. Operators should monitor
    the rate of this signal to detect sustained embedding degradation. text_len is
    the character length of the failing text; dim matches the active model's output
    dimension.
    """
    return Event(
        signal="rag.embedding.chunk.fallback",
        payload={"model": model, "text_len": text_len, "dim": dim},
    )
