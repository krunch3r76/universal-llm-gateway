"""Observation events for journaled scoreboard tip writes."""

from __future__ import annotations

import logging
from typing import Any

from universal_event_bus import Event, event_factory

logger = logging.getLogger(__name__)


@event_factory
def conductor_score_tip_mutated(
    *,
    tip_uri: str,
    prior_tip_sha: str | None,
    tip_sha: str,
    reason: str,
) -> Event:
    """Emit when a journaled tip write succeeds (authority transition)."""
    return Event(
        signal="conductor.score.tip_mutated",
        role="observation",
        scope="global",
        payload={
            "tip_uri": tip_uri,
            "prior_tip_sha": prior_tip_sha,
            "tip_sha": tip_sha,
            "reason": reason,
        },
    )


def emit_conductor_score_tip_mutated(
    *,
    tip_uri: str,
    prior_tip_sha: str | None,
    tip_sha: str,
    reason: str,
) -> Event:
    """Construct and best-effort emit the tip-mutation observation event."""
    event = conductor_score_tip_mutated(
        tip_uri=tip_uri,
        prior_tip_sha=prior_tip_sha,
        tip_sha=tip_sha,
        reason=reason,
    )
    try:
        from scripts.model_manager.observation_event import _emit_sync

        payload: dict[str, Any] = dict(event.payload)
        _emit_sync(event.signal, payload, source="implement_admission")
    except Exception:  # noqa: BLE001 — observation must not fail the write
        logger.debug("conductor.score.tip_mutated UDS emit skipped", exc_info=True)
    return event


__all__ = ["conductor_score_tip_mutated", "emit_conductor_score_tip_mutated"]
