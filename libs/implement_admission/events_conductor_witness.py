"""Observation events for conductor scoreboard witness fold."""

from __future__ import annotations

import logging
from typing import Any

from universal_event_bus import Event, event_factory

logger = logging.getLogger(__name__)


@event_factory
def conductor_score_witness_fold(
    *,
    slug: str,
    rows_done: tuple[str, ...],
    rows_claimed: tuple[str, ...],
    sources: dict[str, str],
) -> Event:
    """Emit when a scoreboard tip Status column is re-rendered from witnesses."""
    return Event(
        signal="conductor.score.witness_fold",
        role="observation",
        scope="global",
        payload={
            "slug": slug,
            "rows_done": list(rows_done),
            "rows_claimed": list(rows_claimed),
            "sources": sources,
        },
    )


def emit_conductor_score_witness_fold(
    *,
    slug: str,
    rows_done: tuple[str, ...],
    rows_claimed: tuple[str, ...],
    sources: dict[str, str],
) -> Event:
    """Construct and best-effort emit the witness-fold observation event."""
    event = conductor_score_witness_fold(
        slug=slug,
        rows_done=rows_done,
        rows_claimed=rows_claimed,
        sources=sources,
    )
    try:
        from scripts.model_manager.observation_event import _emit_sync

        payload: dict[str, Any] = dict(event.payload)
        _emit_sync(event.signal, payload, source="implement_admission")
    except Exception:  # noqa: BLE001 — observation must not fail the fold
        logger.debug("conductor.score.witness_fold UDS emit skipped", exc_info=True)
    return event


__all__ = ["conductor_score_witness_fold", "emit_conductor_score_witness_fold"]
