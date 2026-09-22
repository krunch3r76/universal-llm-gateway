"""Advisory events for density_triage class stamps and implement_ready assertions."""

from __future__ import annotations

import logging
from typing import Any

from universal_event_bus import Event, event_factory

logger = logging.getLogger(__name__)


@event_factory
def density_triage_elevated(
    *,
    entity_id: str,
    prior: str | None,
    current: str,
) -> Event:
    """Emit when a todo's density_triage class rises vs the prior attribute."""
    return Event(
        signal="density_triage.elevated",
        role="observation",
        scope="global",
        payload={
            "entity_id": entity_id,
            "prior": prior,
            "current": current,
        },
    )


@event_factory
def implement_ready_asserted(
    *,
    entity_id: str,
    assertion_id: int | str,
    spec_sha256: str | None,
    evidence_uri: str | None,
) -> Event:
    """Emit when a seat writes the implement_ready assertion at distill gate."""
    return Event(
        signal="implement_ready.asserted",
        role="observation",
        scope="global",
        payload={
            "entity_id": entity_id,
            "assertion_id": assertion_id,
            "spec_sha256": spec_sha256,
            "evidence_uri": evidence_uri,
        },
    )


def _emit_best_effort(signal: str, payload: dict[str, Any]) -> None:
    try:
        from scripts.model_manager.observation_event import _emit_sync

        _emit_sync(signal, payload, source="cortex_store")
    except Exception:  # noqa: BLE001 — observation must not fail the write
        logger.debug("%s UDS emit skipped", signal, exc_info=True)


def emit_density_triage_elevated(
    *,
    entity_id: str,
    prior: str | None,
    current: str,
) -> Event:
    event = density_triage_elevated(entity_id=entity_id, prior=prior, current=current)
    _emit_best_effort(event.signal, dict(event.payload))
    return event


def emit_implement_ready_asserted(
    *,
    entity_id: str,
    assertion_id: int | str,
    spec_sha256: str | None,
    evidence_uri: str | None,
) -> Event:
    event = implement_ready_asserted(
        entity_id=entity_id,
        assertion_id=assertion_id,
        spec_sha256=spec_sha256,
        evidence_uri=evidence_uri,
    )
    _emit_best_effort(event.signal, dict(event.payload))
    return event


__all__ = [
    "density_triage_elevated",
    "emit_density_triage_elevated",
    "emit_implement_ready_asserted",
    "implement_ready_asserted",
]
