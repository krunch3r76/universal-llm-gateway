"""Graft-only transport meta events — enter the Model via apply(), never direct mutation."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event, EventRecord

_TRANSPORT_SOURCE = "ulg://dispatch-monitor/transport"


def replay_truncated_event(
    *,
    connection: str,
    requested_seq: int | None,
    reason: str,
    first_seq: int | None = None,
    ts_unix_ms: int = 0,
) -> EventRecord:
    """Cost warning — replay gap or large payload; fold still receives every event."""
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
