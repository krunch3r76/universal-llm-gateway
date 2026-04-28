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
    failed_chunks: int | None = None,
    attempted_chunks: int | None = None,
    failure_category: str | None = None,
    failure_detail: str | None = None,
    finish_reason: str | None = None,
    top_failure_reasons: list[str] | None = None,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when a file's indexing is deferred for retry on the next watcher sweep.

    Unlike rag.file.indexing.failed (terminal), this signal indicates that extraction
    did not complete but the file was NOT marked as indexed — the watcher will
    re-attempt it automatically. Common reasons: extraction_incomplete (below quality
    threshold), infrastructure_unavailable (extraction model not loaded, model capacity).

    Optional diagnostics provide immediate root-cause context for operators:
    failed/attempted chunk counts, high-level category, finish_reason, and top
    parser failure hints.
    """
    return Event(
        signal="rag.file.retry.deferred",
        payload={
            "file": file,
            "reason": reason,
            **{
                key: value
                for key, value in {
                    "failed_chunks": failed_chunks,
                    "attempted_chunks": attempted_chunks,
                    "failure_category": failure_category,
                    "failure_detail": failure_detail,
                    "finish_reason": finish_reason,
                    "top_failure_reasons": top_failure_reasons,
                    "operation_id": operation_id,
                    "operation": operation,
                }.items()
                if value is not None
            },
        },
    )


@event_factory
def rag_file_indexing_failure_recorded(
    *,
    file: str,
    failure_category: str,
    failure_reason: str,
    attempt_count: int,
    error_type: str | None = None,
    error_head: str | None = None,
) -> Event:
    """Emitted when a file-level indexing failure is persisted to the
    indexing_failures table. Drives operator observability of the permanent
    vs transient classifier decision and the running attempt count.

    error_type: ``type(exc).__qualname__`` of the underlying exception. Lets
        operators discriminate (e.g.) ``ReadTimeout`` from ``RuntimeError``
        without consulting RAG logs.
    error_head: first ~200 chars of ``str(exc)`` — head of the exception
        message, suitable for one-line diagnostic display.
    """
    payload: dict[str, str | int] = {
        "file": file,
        "failure_category": failure_category,
        "failure_reason": failure_reason,
        "attempt_count": attempt_count,
    }
    if error_type is not None:
        payload["error_type"] = error_type
    if error_head is not None:
        payload["error_head"] = error_head
    return Event(
        signal="rag.file.indexing.failure.recorded",
        role="coordination",
        payload=payload,
    )


@event_factory
def rag_file_indexing_failure_skipped(
    *,
    file: str,
    failure_reason: str,
    attempt_count: int,
) -> Event:
    """Emitted when the reconcile loop or initial reindex skips a file
    because a permanent indexing failure is on record with unchanged
    mtime/size. Coordination signal — gates admission to the worker queue."""
    return Event(
        signal="rag.file.indexing.failure.skipped",
        role="coordination",
        payload={
            "file": file,
            "failure_reason": failure_reason,
            "attempt_count": attempt_count,
        },
    )


@event_factory
def rag_file_indexing_failure_cleared(
    *,
    file: str,
    reason: str,
) -> Event:
    """Emitted when a row in indexing_failures is removed. reason ∈
    {'indexed_successfully', 'source_deleted', 'operator_cleared'}. Fires
    only after a row was actually deleted (rowcount > 0) to avoid noisy
    no-op events on first-time indexing."""
    return Event(
        signal="rag.file.indexing.failure.cleared",
        role="coordination",
        payload={"file": file, "reason": reason},
    )


@event_factory
def rag_file_indexing_failure_retry_requested(
    *,
    file: str,
    scheduled: bool,
) -> Event:
    """Emitted when an operator requests a retry via the admin API. scheduled
    indicates whether the watcher accepted the reindex admission — useful for
    distinguishing operator intent from actual scheduling outcome."""
    return Event(
        signal="rag.file.indexing.failure.retry.requested",
        role="coordination",
        payload={"file": file, "scheduled": scheduled},
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
def rag_property_index_unavailable(*, file: str) -> Event:
    """Emitted when indexing continues without a property index instance."""
    return Event(
        signal="rag.property.index.unavailable",
        payload={"file": file},
    )


@event_factory
def rag_contextualization_started(
    *,
    file: str,
    chunk_count: int,
    model: str,
    max_concurrency: int,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted before per-chunk contextualization requests are dispatched."""
    return Event(
        signal="rag.contextualization.started",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "model": model,
            "max_concurrency": max_concurrency,
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
def rag_contextualization_completed(
    *,
    file: str,
    chunk_count: int,
    successful: int,
    failed: int,
    duration_seconds: float,
    model: str,
    max_concurrency: int,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted after all contextualization requests settle for a file."""
    return Event(
        signal="rag.contextualization.completed",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "successful": successful,
            "failed": failed,
            "duration_seconds": duration_seconds,
            "model": model,
            "max_concurrency": max_concurrency,
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
def rag_contextualization_partial(
    *,
    file: str,
    total_chunks: int,
    failed_chunks: int,
    successful_chunks: int,
    model: str,
    first_failure: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when contextualization completed with partial chunk failures.

    The file is still indexed — failed chunks are embedded without context
    prefix (modest retrieval-quality regression on those chunks only). They
    remain cache misses and will be re-contextualized on the next reindex.

    Distinct from rag.contextualization.completed (always emitted) — this
    signal fires only when failed_chunks > 0, giving operators a single-line
    indicator of which files have degraded contextualization.
    """
    payload: dict[str, str | int] = {
        "file": file,
        "total_chunks": total_chunks,
        "failed_chunks": failed_chunks,
        "successful_chunks": successful_chunks,
        "model": model,
        "first_failure": first_failure[:200],
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualization.partial",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualization_tail_abandoned(
    *,
    file: str,
    total_chunks: int,
    completed_chunks: int,
    abandoned_chunks: int,
    successful_chunks: int,
    failed_chunks: int,
    model: str,
    idle_seconds: float,
    tail_idle_timeout_s: float,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when RAG stops waiting for straggler contextualization chunks."""
    payload: dict[str, str | int | float] = {
        "file": file,
        "total_chunks": total_chunks,
        "completed_chunks": completed_chunks,
        "abandoned_chunks": abandoned_chunks,
        "successful_chunks": successful_chunks,
        "failed_chunks": failed_chunks,
        "model": model,
        "idle_seconds": idle_seconds,
        "tail_idle_timeout_s": tail_idle_timeout_s,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualization.tail.abandoned",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualization_exception_recorded(
    *,
    file: str,
    exception_id: int,
    total_chunks: int,
    cache_miss_chunks: int,
    successful_chunks: int,
    failed_chunks: int,
    abandoned_chunks: int,
    model: str,
    first_failure: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when degraded contextualization diagnostics are durably stored."""
    payload: dict[str, str | int] = {
        "file": file,
        "exception_id": exception_id,
        "total_chunks": total_chunks,
        "cache_miss_chunks": cache_miss_chunks,
        "successful_chunks": successful_chunks,
        "failed_chunks": failed_chunks,
        "abandoned_chunks": abandoned_chunks,
        "model": model,
        "first_failure": first_failure[:200],
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualization.exception.recorded",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualization_exception_record_failed(
    *,
    file: str,
    model: str,
    error: str,
    operation_id: str | None = None,
    operation: str | None = None,
) -> Event:
    """Emitted when degraded contextualization diagnostics could not be stored."""
    payload: dict[str, str] = {
        "file": file,
        "model": model,
        "error": error[:500],
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    if operation is not None:
        payload["operation"] = operation
    return Event(
        signal="rag.contextualization.exception.record.failed",
        role="observation",
        payload=payload,
    )


@event_factory
def rag_contextualization_applied(*, file: str, chunk_count: int, model: str) -> Event:
    """Emitted when contextualized chunk prefixes are applied before embedding."""
    return Event(
        signal="rag.contextualization.applied",
        payload={"file": file, "chunk_count": chunk_count, "model": model},
    )


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


@event_factory
def rag_indexing_failure_persist_failed(
    *,
    file: str,
    error: str,
) -> Event:
    """Emitted when the attempt to persist an indexing failure record itself
    raises an exception. The original indexing failure is not lost — this
    signal indicates a secondary persistence failure on the failure-of-failure
    path in _record_indexing_failure_best_effort.

    error: ``type(exc).__qualname__: str(exc)`` of the persistence exception.
    """
    return Event(
        signal="rag.indexing.failure.persist.failed",
        role="observation",
        payload={"file": file, "error": error},
    )
