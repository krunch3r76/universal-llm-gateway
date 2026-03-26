"""RAG indexing and storage event factories."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def rag_property_index_rebuilt(
    *,
    collection: str,
    count: int,
) -> Event:
    return Event(
        signal="rag.property.index.rebuilt",
        payload={"collection": collection, "count": count},
    )


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
def rag_file_retry_deferred(
    *,
    file: str,
    reason: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when a file's indexing is deferred for retry on the next watcher sweep.

    Unlike rag.file.indexing.failed (terminal), this signal indicates that extraction
    did not complete but the file was NOT marked as indexed — the watcher will
    re-attempt it automatically. Common reasons: extraction_incomplete (below quality
    threshold), infrastructure_unavailable (extraction model not loaded, model capacity).
    """
    return Event(
        signal="rag.file.retry.deferred",
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
def rag_file_deletion_failed(
    *,
    file: str,
    error: str,
) -> Event:
    """Emitted when watcher-triggered file deletion cleanup fails."""
    return Event(
        signal="rag.file.deletion.failed",
        payload={"file": file, "error": error},
    )


@event_factory
def rag_article_content_hash_mismatch(
    *,
    file: str,
    expected_hash: str,
    actual_hash: str,
) -> Event:
    """Emitted when source bytes do not match article registry content_hash."""
    return Event(
        signal="rag.article.content.hash.mismatch",
        payload={
            "file": file,
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
        },
    )


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
def rag_property_index_unavailable(*, file: str) -> Event:
    """Emitted when indexing continues without a property index instance."""
    return Event(
        signal="rag.property.index.unavailable",
        payload={"file": file},
    )


@event_factory
def rag_contextualization_applied(*, file: str, chunk_count: int, model: str) -> Event:
    """Emitted when contextualized chunk prefixes are applied before embedding."""
    return Event(
        signal="rag.contextualization.applied",
        payload={"file": file, "chunk_count": chunk_count, "model": model},
    )


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
) -> Event:
    """Emitted immediately before chunk rows are upserted into ChromaDB."""
    return Event(
        signal="rag.chroma.upsert.started",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            **({"operation": operation} if operation is not None else {}),
        },
    )


@event_factory
def rag_chroma_upsert_completed(
    *,
    file: str,
    operation_id: str,
    chunk_count: int,
    operation: str | None = None,
) -> Event:
    """Emitted after chunk rows are persisted to ChromaDB."""
    return Event(
        signal="rag.chroma.upsert.completed",
        payload={
            "file": file,
            "operation_id": operation_id,
            "chunk_count": chunk_count,
            **({"operation": operation} if operation is not None else {}),
        },
    )


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


@event_factory
def rag_html_normalization_started(*, file: str) -> Event:
    """Emitted when HTML ingest enters the normalization pipeline."""
    return Event(signal="rag.html.normalization.started", payload={"file": file})


@event_factory
def rag_html_normalization_completed(
    *,
    file: str,
    output_chars: int,
) -> Event:
    """Emitted when HTML normalization succeeds with deterministic markdown output."""
    return Event(
        signal="rag.html.normalization.completed",
        payload={"file": file, "output_chars": output_chars},
    )


@event_factory
def rag_html_normalization_failed(*, file: str, error: str) -> Event:
    """Emitted when HTML normalization fails and file is skipped from indexing."""
    return Event(
        signal="rag.html.normalization.failed",
        payload={"file": file, "error": error},
    )


@event_factory
def rag_directory_cleared(
    *,
    path: str,
    sources_cleared: int,
    chunks_cleared: int,
) -> Event:
    """Emitted after all chunks for sources under a directory are deleted.

    Fired by POST /clear_directory and by reindex_directory when force=True
    (upfront clear before re-indexing).
    """
    return Event(
        signal="rag.directory.cleared",
        payload={
            "path": path,
            "sources_cleared": sources_cleared,
            "chunks_cleared": chunks_cleared,
        },
    )


@event_factory
def rag_directory_index_started(
    *,
    path: str,
    total_files: int,
) -> Event:
    """Emitted before concurrent directory indexing dispatch begins.

    ∀ concurrent reindex: emitted once, listing the directory and file count
    so an interrupted session is diagnosable via the event log.
    total_files: number of files that will be dispatched (before any are processed).
    """
    return Event(
        signal="rag.directory.index.started",
        payload={"path": path, "total_files": total_files},
    )


@event_factory
def rag_directory_index_completed(
    *,
    path: str,
    total_files: int,
    indexed: int,
    deleted: int,
    unchanged: int,
    duplicates: int,
    errors: int,
) -> Event:
    """Emitted after all files in a directory index/reindex have been processed.

    Absence of this signal following rag.directory.index.started indicates
    an interrupted session — re-run reindex_directory to recover.
    errors: files that raised an exception and were passed to on_index_error.
    """
    return Event(
        signal="rag.directory.index.completed",
        payload={
            "path": path,
            "total_files": total_files,
            "indexed": indexed,
            "deleted": deleted,
            "unchanged": unchanged,
            "duplicates": duplicates,
            "errors": errors,
        },
    )
