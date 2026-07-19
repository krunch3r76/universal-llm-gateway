"""RAG indexing event factories — file lifecycle transitions."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def rag_file_indexed(
    *,
    file: str,
    deleted: int,
    indexed: int,
    duration_seconds: float = 0.0,
    batch_start_ts: str | None = None,
    document_metadata: dict[str, Any] | None = None,
    noise_chunks: int | None = None,
    processing_seconds: float | None = None,
    queue_wait_seconds: float | None = None,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted after a file is fully indexed into both ChromaDB and the property index.

    batch_start_ts: optional ISO-8601 when extraction started (enables per-file wall-clock duration).
    document_metadata: optional dict for document-specific fields (e.g. article_title, article_authors,
        article_venue, published_date, article_doi when file is in article registry).
    noise_chunks: optional count of chunks tagged ``is_noise`` (or legacy ``is_bibliography``) for this file.
    processing_seconds: optional Stargate-derived work time (post-queue).
    queue_wait_seconds: optional time from pipeline step start to first inference started.
    """
    return Event(
        signal="rag.file.indexed",
        payload={
            "file": file,
            "deleted": deleted,
            "indexed": indexed,
            "duration_seconds": duration_seconds,
            **{
                key: value
                for key, value in {
                    "batch_start_ts": batch_start_ts,
                    "document_metadata": document_metadata,
                    "noise_chunks": noise_chunks,
                    "processing_seconds": processing_seconds,
                    "queue_wait_seconds": queue_wait_seconds,
                    "operation_id": operation_id,
                    "operation": operation,
                }.items()
                if value is not None
            },
        },
    )


@event_factory
def rag_file_deleted(
    *,
    file: str,
    deleted: int,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when all chunks for a file are deleted with no replacement (empty file)."""
    return Event(
        signal="rag.file.deleted",
        payload={
            "file": file,
            "deleted": deleted,
            **{
                key: value
                for key, value in {
                    "operation_id": operation_id,
                    "operation": operation,
                }.items()
                if value is not None
            },
        },
    )


@event_factory
def rag_file_skipped(
    *,
    file: str,
    reason: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when a file is skipped during indexing (unchanged or duplicate PDF)."""
    return Event(
        signal="rag.file.skipped",
        payload={
            "file": file,
            "reason": reason,
            **{
                key: value
                for key, value in {
                    "operation_id": operation_id,
                    "operation": operation,
                }.items()
                if value is not None
            },
        },
    )


@event_factory
def rag_file_indexing_failed(
    *,
    file: str,
    error: str,
    model: str | None = None,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when an unhandled error aborts file indexing."""
    payload: dict[str, str] = {"file": file, "error": error}
    if model is not None:
        payload["model"] = model
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.file.indexing.failed",
        payload=payload,
    )


@event_factory
def rag_property_index_unavailable(*, file: str) -> Event:
    """Emitted when indexing continues without a property index instance."""
    return Event(
        signal="rag.property.index.unavailable",
        payload={"file": file},
    )
