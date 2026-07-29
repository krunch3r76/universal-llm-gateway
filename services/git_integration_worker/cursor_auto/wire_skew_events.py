"""Wire-skew observation for tolerant enqueue boundaries under deploy-order skew.

When MCP sends additive fields the GIW receiver has not yet learned, dropped keys
are counted in-process and surfaced on liveness. Hot-path emits are latched so
structural skew does not flood the event server.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

_LATCH_WINDOW_S = 60.0
_lock = threading.Lock()
_aggregate: dict[str, int] = defaultdict(int)
_latch_until: dict[tuple[str, frozenset[str]], float] = {}


@event_factory
def GiwCursorAutoWireSkew(  # noqa: N802
    boundary: str,
    dropped_fields: tuple[str, ...],
    sender: str,
    count: int,
) -> Event:
    """Signal: giw.cursor_auto.wire_skew"""
    return Event(
        signal="giw.cursor_auto.wire_skew",
        payload={
            "boundary": boundary,
            "dropped_fields": list(dropped_fields),
            "sender": sender,
            "count": count,
        },
        scope="node",
        role="observation",
    )


def get_wire_skew_aggregate() -> dict[str, int]:
    """Return a snapshot of in-process dropped-field counters keyed by boundary."""
    with _lock:
        return dict(_aggregate)


def note_dropped_fields(
    *,
    boundary: str,
    dropped_fields: list[str],
    sender: str,
) -> None:
    """Record dropped wire keys; emit a latched observation when the latch opens."""
    if not dropped_fields:
        return
    field_set = frozenset(dropped_fields)
    latch_key = (boundary, field_set)
    now = time.monotonic()
    emit_now = False
    count = 0
    with _lock:
        _aggregate[boundary] += len(dropped_fields)
        count = _aggregate[boundary]
        until = _latch_until.get(latch_key, 0.0)
        if now >= until:
            _latch_until[latch_key] = now + _LATCH_WINDOW_S
            emit_now = True
    if emit_now:
        emit_frontier_event(
            GiwCursorAutoWireSkew(
                boundary=boundary,
                dropped_fields=tuple(sorted(field_set)),
                sender=sender,
                count=count,
            )
        )
    logger.info(
        "cursor-auto wire_skew boundary=%s sender=%s dropped=%s aggregate=%s",
        boundary,
        sender,
        sorted(field_set),
        count,
    )


def reset_wire_skew_state_for_tests() -> None:
    """Clear aggregate and latch state — test hook only."""
    with _lock:
        _aggregate.clear()
        _latch_until.clear()


__all__ = [
    "get_wire_skew_aggregate",
    "note_dropped_fields",
    "reset_wire_skew_state_for_tests",
]
