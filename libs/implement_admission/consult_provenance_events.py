"""Observation event for todo-keyed consult-provenance record commit.

Advisory only — never an implement-admission input. Distinct from
``manage.charter.tick.consult.harvested`` (charter-window harvest).
"""

from __future__ import annotations

import logging
from typing import Any

from universal_event_bus import Event, event_factory

logger = logging.getLogger(__name__)


@event_factory
def consult_provenance_recorded(
    *,
    todo: str,
    consult_thread: str,
    archive_sha256: str,
    adjudication_assertion_id: int,
    written_by: str,
) -> Event:
    """Build the authority-transition event for a todo-keyed provenance write."""
    return Event(
        signal="consult.provenance.recorded",
        role="observation",
        scope="node",
        payload={
            "todo": todo,
            "consult_thread": consult_thread,
            "archive_sha256": archive_sha256,
            "adjudication_assertion_id": adjudication_assertion_id,
            "written_by": written_by,
        },
    )


def emit_consult_provenance_recorded(
    *,
    todo: str,
    consult_thread: str,
    archive_sha256: str,
    adjudication_assertion_id: int,
    written_by: str,
) -> Event:
    """Construct the observation event and best-effort emit it over UDS."""
    event = consult_provenance_recorded(
        todo=todo,
        consult_thread=consult_thread,
        archive_sha256=archive_sha256,
        adjudication_assertion_id=adjudication_assertion_id,
        written_by=written_by,
    )
    try:
        from scripts.model_manager.observation_event import _emit_sync

        payload: dict[str, Any] = dict(event.payload)
        _emit_sync(event.signal, payload, source="implement_admission")
    except Exception:  # noqa: BLE001 — observation must not fail the writer
        logger.debug("consult.provenance.recorded UDS emit skipped", exc_info=True)
    return event


__all__ = ["consult_provenance_recorded", "emit_consult_provenance_recorded"]
