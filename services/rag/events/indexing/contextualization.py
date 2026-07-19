"""RAG indexing event factories — file-level contextualization flow."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
