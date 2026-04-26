"""RAG extraction admission (coordination) event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_extraction_admission_closed(
    *,
    pipeline_id: str,
    reason: str,
    active_reasons: list[str],
    signal: str,
) -> Event:
    """Emitted when the extraction admission gate transitions OPEN → CLOSED."""
    return Event(
        signal="rag.extraction.admission.closed",
        payload={
            "pipeline_id": pipeline_id,
            "reason": reason,
            "active_reasons": list(active_reasons),
            "signal": signal,
        },
        role="coordination",
        scope="node",
    )


@event_factory
def rag_extraction_admission_opened(
    *,
    pipeline_id: str,
    cleared_reason: str,
    signal: str,
    closed_seconds: float,
) -> Event:
    """Emitted when the last close-reason clears and the gate reopens."""
    return Event(
        signal="rag.extraction.admission.opened",
        payload={
            "pipeline_id": pipeline_id,
            "cleared_reason": cleared_reason,
            "signal": signal,
            "closed_seconds": closed_seconds,
        },
        role="coordination",
        scope="node",
    )


@event_factory
def rag_extraction_admission_timeout(
    *,
    pipeline_id: str,
    waited_seconds: float,
    active_reasons: list[str],
) -> Event:
    """Emitted when the worker's wait_for_admission times out and proceeds."""
    return Event(
        signal="rag.extraction.admission.timeout",
        payload={
            "pipeline_id": pipeline_id,
            "waited_seconds": waited_seconds,
            "active_reasons": list(active_reasons),
        },
        role="coordination",
        scope="node",
    )
