"""Persist extraction failure and emit queue event."""

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
