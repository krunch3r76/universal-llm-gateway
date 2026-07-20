"""RAG indexing event factories — indexing failure and retry tracking."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
def rag_entity_gate_io_failed(
    *,
    operation: str,
    error: str,
) -> Event:
    """Emitted when EntityAdmissionGate HTTP/WS I/O fails.

    operation: ``"refresh"`` (cortex-api source-paths snapshot) or
    ``"subscribe"`` (Event Service WebSocket reconnect loop).
    error: ``str(exc)`` from the caught exception.
    """
    return Event(
        signal="rag.entity.gate.io.failed",
        payload={"operation": operation, "error": error},
    )


@event_factory
def rag_file_indexing_gated(
    *,
    file: str,
    layer: str,
) -> Event:
    """Emitted when a file in an entity-gated watch root is skipped at index
    time because no cortex entity points at it via source_uri (thread 1136
    A1/A5). Coordination signal — NOT a failure row, so it does not pollute
    indexing_failures or conflate "out of scope" with "broken".

    layer ∈ {"watcher_sweep", "index_funnel"} — whether Layer 1
    (WatcherManager._should_attempt) or Layer 2 (indexing._index_file_impl)
    caught it. At most once per source per sweep: sweeps short-circuit at
    Layer 1, so a sweep-skipped file never reaches Layer 2 (no double emission).
    """
    return Event(
        signal="rag.file.indexing.gated",
        role="coordination",
        payload={"file": file, "layer": layer},
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
        signal="rag.file.indexing.retry.requested",
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
