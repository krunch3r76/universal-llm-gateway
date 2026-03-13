"""RAG extraction event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_extraction_completed(
    *,
    chunk_id: str,
    entities: int,
    topics: int,
) -> Event:
    return Event(
        signal="rag.extraction.completed",
        payload={"chunk_id": chunk_id, "entities": entities, "topics": topics},
    )


@event_factory
def rag_extraction_failed(
    *,
    chunk_id: str,
    error: str,
) -> Event:
    return Event(
        signal="rag.extraction.failed",
        payload={"chunk_id": chunk_id, "error": error},
    )


@event_factory
def rag_extraction_permanently_skipped(
    *,
    chunk_id: str,
    source: str,
    attempt_count: int,
) -> Event:
    """Emitted when a chunk crosses max_extraction_attempts and is permanently abandoned.

    ∀ chunk_id: emitted exactly once, on the attempt that causes attempt_count >= max_attempts.
    Persisted in failed_extractions.permanent = 1.
    """
    return Event(
        signal="rag.extraction.permanently.skipped",
        payload={
            "chunk_id": chunk_id,
            "source": source,
            "attempt_count": attempt_count,
        },
    )


@event_factory
def rag_extraction_batch_started(
    *,
    file: str,
    chunk_count: int,
) -> Event:
    return Event(
        signal="rag.extraction.batch.started",
        payload={"file": file, "chunk_count": chunk_count},
    )


@event_factory
def rag_extraction_batch_completed(
    *,
    file: str,
    chunk_count: int,
    successful: int,
    written: int,
    duration_seconds: float,
    extraction_model: str | None = None,
) -> Event:
    """Emitted after an extraction batch finishes.

    successful: chunks for which the pipeline returned a valid result (may be
        less than chunk_count on partial pipeline failure).
    written: chunks whose extraction metadata was committed (0 when the
        all-or-nothing rule fires due to partial failure; equals successful
        when all chunks succeed).
    extraction_model: model id used for extraction when configured.
    """
    return Event(
        signal="rag.extraction.batch.completed",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "successful": successful,
            "written": written,
            "duration_seconds": duration_seconds,
            **(
                {}
                if extraction_model is None
                else {"extraction_model": extraction_model}
            ),
        },
    )


@event_factory
def rag_extraction_model_mismatch(
    *,
    file: str,
    expected_model: str,
    chunk_count: int,
) -> Event:
    """Emitted when re-extraction is triggered because existing chunks have different or missing extraction_model."""
    return Event(
        signal="rag.extraction.model.mismatch",
        payload={
            "file": file,
            "expected_model": expected_model,
            "chunk_count": chunk_count,
        },
    )


@event_factory
def rag_extraction_recovery_completed(
    *,
    file: str,
    entities: int,
    topics: int,
) -> Event:
    """Emitted when a recovery pass for missing extraction metadata completes successfully."""
    return Event(
        signal="rag.extraction.recovery.completed",
        payload={"file": file, "entities": entities, "topics": topics},
    )


@event_factory
def rag_extraction_recovery_skipped(
    *,
    file: str,
    reason: str,
) -> Event:
    """Emitted when recovery was skipped (e.g. no documents in ChromaDB, all chunks permanently failed)."""
    return Event(
        signal="rag.extraction.recovery.skipped",
        payload={"file": file, "reason": reason},
    )


@event_factory
def rag_extraction_recovery_failed(*, file: str, reason: str) -> Event:
    """Emitted when recovery runs but cannot commit extraction metadata."""
    return Event(
        signal="rag.extraction.recovery.failed",
        payload={"file": file, "reason": reason},
    )


@event_factory
def rag_extraction_batch_skipped(
    *,
    file: str,
    chunk_count: int,
    skipped_count: int,
    max_attempts: int,
) -> Event:
    """Emitted when all chunks in a batch have exceeded max_extraction_attempts.

    ∀ chunk_id ∈ batch: attempt_count >= max_attempts ⟹ batch skipped entirely.
    No pipeline call is made; extraction is permanently abandoned for these chunks.
    """
    return Event(
        signal="rag.extraction.batch.skipped",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "skipped_count": skipped_count,
            "max_attempts": max_attempts,
        },
    )


@event_factory
def rag_extraction_batch_timed_out(
    *,
    file: str,
    chunk_count: int,
    timeout_seconds: float,
    duration_seconds: float,
) -> Event:
    """Emitted when an extraction batch exceeds its dynamic per-batch timeout.

    Timeout scales with chunk_count and configured overhead. All chunks in the
    batch are recorded as transient failures and retried on a future sweep.
    """
    return Event(
        signal="rag.extraction.batch.timed.out",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "timeout_seconds": timeout_seconds,
            "duration_seconds": duration_seconds,
        },
    )
