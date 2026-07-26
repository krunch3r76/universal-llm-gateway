"""Graft-only transport meta events — enter the Model via apply(), never direct mutation."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord

_TRANSPORT_SOURCE = "ulg://dispatch-monitor/transport"


def fold_status_transport_event(
    *,
    fold_status: str,
    reason: str,
    connection: str | None = None,
    ts_unix_ms: int = 0,
) -> EventRecord:
    """Fold posture transitions driven by subscribe truncation / reseed."""
    payload: dict[str, object] = {"fold_status": fold_status, "reason": reason}
    if connection:
        payload["connection"] = connection
    return Event(
        signal=signals.MONITOR_SEED_FOLD_STATUS,
        ts_unix_ms=ts_unix_ms,
        payload=payload,
        source=_TRANSPORT_SOURCE,
    )


def replay_truncated_event(
    *,
    connection: str,
    requested_seq: int | None,
    reason: str,
    first_seq: int | None = None,
    ts_unix_ms: int = 0,
) -> EventRecord:
    """GX1 — replay window could not be satisfied; surfaces via attention derivation."""
    return Event(
        signal=signals.MONITOR_TRANSPORT_REPLAY_TRUNCATED,
        ts_unix_ms=ts_unix_ms,
        payload={
            "connection": connection,
            "requested_seq": requested_seq,
            "first_seq": first_seq,
            "reason": reason,
        },
        source=_TRANSPORT_SOURCE,
        subject=connection,
    )
