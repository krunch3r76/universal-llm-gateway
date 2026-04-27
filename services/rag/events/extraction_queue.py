"""RAG extraction queue event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_extraction_source_claimed(
    *,
    source: str,
    attempts: int,
    queued_at: str,
    claimed_at: str,
) -> Event:
    """Emit when the extraction worker atomically claims a source row."""
    return Event(
        signal="rag.extraction.source.claimed",
        payload={
            "source": source,
            "attempts": attempts,
            "queued_at": queued_at,
            "claimed_at": claimed_at,
        },
    )


@event_factory
def rag_extraction_source_completed(
    *,
    source: str,
    duration_seconds: float,
) -> Event:
    """Emit when a source is fully extracted and removed from the queue."""
    return Event(
        signal="rag.extraction.source.completed",
        payload={
            "source": source,
            "duration_seconds": duration_seconds,
        },
    )


@event_factory
def rag_extraction_source_failed(
    *,
    source: str,
    failure_category: str,
    error_type: str,
    increment_attempt: bool,
) -> Event:
    """Emit when a source extraction fails and remains queued for retry or exhaustion."""
    return Event(
        signal="rag.extraction.source.failed",
        payload={
            "source": source,
            "failure_category": failure_category,
            "error_type": error_type,
            "increment_attempt": increment_attempt,
        },
    )


@event_factory
def rag_extraction_claim_recovered(
    *,
    source: str,
    claimed_at: str,
    claimed_age_seconds: float,
) -> Event:
    """Emit when startup clears a claim left behind by a previous RAG process."""
    return Event(
        signal="rag.extraction.claim.recovered",
        payload={
            "source": source,
            "claimed_at": claimed_at,
            "claimed_age_seconds": claimed_age_seconds,
        },
    )


@event_factory
def rag_extraction_queue_woken(
    *,
    pipeline_id: str,
    reset_count: int,
) -> Event:
    """Emit when extraction model availability wakes cooling-off queue items.

    Fired when ``model.available`` arrives for the extraction pipeline and
    cooling-off items whose last failure was not capacity-related are reset
    to immediately eligible so the worker picks them up without waiting for
    the backoff window.
    """
    return Event(
        signal="rag.extraction.queue.woken",
        payload={
            "pipeline_id": pipeline_id,
            "reset_count": reset_count,
        },
    )
