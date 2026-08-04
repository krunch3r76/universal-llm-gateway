"""RAG indexing event factories — embed, chroma, property, commit, hints pipeline."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_embed_started(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    operation: str | None = None,
) -> Event:
    """Emitted immediately before chunk embeddings are requested for indexing."""
    return Event(
        signal="rag.embed.started",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_embed_completed(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    operation: str | None = None,
) -> Event:
    """Emitted after chunk embeddings return for indexing."""
    return Event(
        signal="rag.embed.completed",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_chroma_upsert_started(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    operation: str | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
) -> Event:
    """Emitted immediately before chunk rows are upserted into ChromaDB."""
    payload: dict[str, str | int] = {
        "file": file,
        "operation_id": operation_id,
        "chunk_count": chunk_count,
    }
    if operation is not None:
        payload["operation"] = operation
    if batch_index is not None:
        payload["batch_index"] = batch_index
    if batch_total is not None:
        payload["batch_total"] = batch_total
    return Event(signal="rag.chroma.upsert.started", payload=payload)


@event_factory
def rag_chroma_upsert_completed(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    operation: str | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
) -> Event:
    """Emitted after chunk rows are persisted to ChromaDB."""
    payload: dict[str, str | int] = {
        "file": file,
        "operation_id": operation_id,
        "chunk_count": chunk_count,
    }
    if operation is not None:
        payload["operation"] = operation
    if batch_index is not None:
        payload["batch_index"] = batch_index
    if batch_total is not None:
        payload["batch_total"] = batch_total
    return Event(signal="rag.chroma.upsert.completed", payload=payload)


@event_factory
def rag_property_write_started(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    property_entries: int,
    operation: str | None = None,
) -> Event:
    """Emitted before SQLite-backed FTS and property metadata writes begin."""
    return Event(
        signal="rag.property.write.started",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            "property_entries": property_entries,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_property_write_completed(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    property_entries: int,
    operation: str | None = None,
) -> Event:
    """Emitted after SQLite-backed FTS and property metadata writes finish."""
    return Event(
        signal="rag.property.write.completed",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            "property_entries": property_entries,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_source_commit_started(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    stale_chunks: int,
    operation: str | None = None,
) -> Event:
    """Emitted before final source-level metadata commit and stale cleanup begin."""
    return Event(
        signal="rag.source.commit.started",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            "stale_chunks": stale_chunks,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_source_commit_completed(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    stale_chunks: int,
    operation: str | None = None,
) -> Event:
    """Emitted after final source-level metadata commit and stale cleanup finish."""
    return Event(
        signal="rag.source.commit.completed",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            "stale_chunks": stale_chunks,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_hints_update_started(
    *,
    file: str,
    operation_id: str,
    operation: str | None = None,
) -> Event:
    """Emitted before post-index corpus-hints refresh begins."""
    return Event(
        signal="rag.hints.update.started",
        payload={
            "file": file,
            "operation_id": operation_id,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_embed_diff_evaluated(
    *,
    file: str,
    operation_id: str,
    total_chunks: int,
    processed_chunks: int,
    skipped_chunks: int,
    legacy_id_count: int = 0,
    operation: str | None = None,
) -> Event:
    """Emitted after embed diff-gate partition (aggregate skip/process counts)."""
    payload: dict[str, str | int] = {
        "file": file,
        "operation_id": operation_id,
        "total_chunks": total_chunks,
        "processed_chunks": processed_chunks,
        "skipped_chunks": skipped_chunks,
        "legacy_id_count": legacy_id_count,
    }
    if operation is not None:
        payload["operation"] = operation
    return Event(signal="rag.embed.diff.evaluated", payload=payload)


@event_factory
def rag_hints_update_completed(
    *,
    file: str,
    operation_id: str,
    operation: str | None = None,
) -> Event:
    """Emitted after post-index corpus-hints refresh returns."""
    return Event(
        signal="rag.hints.update.completed",
        payload={
            "file": file,
            "operation_id": operation_id,
            **({"operation": operation} if operation is not None else {}),
        },
    )
