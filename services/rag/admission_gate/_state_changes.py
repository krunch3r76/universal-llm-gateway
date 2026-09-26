"""Gate open/close state mutations and first-burst emission.

Called from ``admission_gate/_signals.py``. A gate stays CLOSED while any close
reason (e.g. ``model.loading``, ``capacity.admission``, ``gateway:<id>``) is
active and reopens only when the reason set is empty, resetting the admitted
worker count. The first ``model.loading`` close per model schedules a
first-burst observation event.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gate import AdmissionGate

logger = logging.getLogger(__name__)


def _close_gate(
    gate: AdmissionGate,
    key: str,
    *,
    reason: str,
    signal: str,
    payload: dict[str, object],
) -> None:
    ev = gate._tracked[key]
    reasons = gate._closed_reasons.setdefault(key, set())
    already_closed_for_reason = reason in reasons
    reasons.add(reason)
    # First-burst measurement: on the first OPEN→CLOSED transition via a
    # cold-load window (reason="model.loading"), capture workers_admitted
    # and schedule async emission of rag.admission.first.burst.observed.
    # Scheduled via create_task (valid: called from within the async
    # subscriber loop, so the event loop is running). Emits once per model.
    if (
        reason == "model.loading"
        and key not in gate._first_burst_emitted
        and gate._event_bus is not None
    ):
        gate._first_burst_emitted.add(key)
        workers = gate._workers_admitted.get(key, 0)
        asyncio.create_task(
            gate._emit_first_burst_observed(key, workers),
            name=f"rag-admission-burst-{key}",
        )
    if ev.is_set():
        ev.clear()
        logger.info(
            "AdmissionGate: %s CLOSED (signal=%s, reason=%s, active_reasons=%s)",
            key,
            signal,
            payload.get("reason", reason),
            sorted(reasons),
        )
    elif not already_closed_for_reason:
        logger.info(
            "AdmissionGate: %s remains CLOSED (signal=%s, reason=%s, active_reasons=%s)",
            key,
            signal,
            payload.get("reason", reason),
            sorted(reasons),
        )


def _open_gate(
    gate: AdmissionGate,
    key: str,
    *,
    reason: str,
    signal: str,
    payload: dict[str, object],
) -> None:
    ev = gate._tracked[key]
    reasons = gate._closed_reasons.setdefault(key, set())
    reasons.discard(reason)
    if not reasons and not ev.is_set():
        ev.set()
        gate._workers_admitted[key] = 0
        logger.info(
            "AdmissionGate: %s OPEN (signal=%s, reason=%s)",
            key,
            signal,
            payload.get("reason", reason),
        )
