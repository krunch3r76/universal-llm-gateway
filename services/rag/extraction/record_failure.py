"""Persist an extraction-queue failure and emit the matching queue event.

``record_source_failure`` is the single failure sink for
``extraction.worker_loop``: it calls ``PropertyIndex.fail_extraction`` (which
may or may not burn a retry attempt, per ``increment_attempt``) and then
publishes ``rag.extraction.source.failed`` when an event bus is wired, keeping
queue state and observability in lockstep.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.rag.events.extraction_queue import rag_extraction_source_failed

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from services.rag.property_index import PropertyIndex


async def record_source_failure(
    *,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
    source: str,
    increment_attempt: bool,
    failure_category: str,
    error: str,
    error_type: str,
) -> None:
    """Persist source failure and emit the matching queue transition event."""
    await property_index.fail_extraction(
        source,
        increment_attempt=increment_attempt,
        failure_category=failure_category,
        error=error,
        error_type=error_type,
    )
    if event_bus is not None:
        await event_bus.publish_nowait(
            rag_extraction_source_failed(
                source=source,
                failure_category=failure_category,
                error_type=error_type,
                increment_attempt=increment_attempt,
            )
        )
