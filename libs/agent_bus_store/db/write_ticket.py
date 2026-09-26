"""In-process FIFO write ticket — deque, mutex, and thread-local nesting state."""

from __future__ import annotations

import collections
import os
import threading
from dataclasses import dataclass

from agent_bus_store.events.write_queue import (
    emit_write_queue_armed,
    emit_write_queue_cleared,
)

_deque: collections.deque[threading.Event] = collections.deque()
_mutex = threading.Lock()


@dataclass
class WriteTicketState:
    """Per-thread write connection and ticket nesting."""

    conn: object | None = None
    depth: int = 0
    event: threading.Event | None = None


_tls = threading.local()


def _state() -> WriteTicketState:
    if not hasattr(_tls, "agent_bus_write"):
        _tls.agent_bus_write = WriteTicketState()
    return _tls.agent_bus_write


def _queue_depth_threshold() -> int | None:
    """Positive queue-depth threshold from env, or None when unset/invalid."""
    raw = os.environ.get("AGENT_BUS_WRITE_QUEUE_DEPTH_THRESHOLD")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = int(str(raw).strip())
    except ValueError:
        return None
    if val <= 0:
        return None
    return val


def _maybe_emit_armed(depth: int) -> None:
    threshold = _queue_depth_threshold()
    if threshold is None or depth <= threshold:
        return
    emit_write_queue_armed(depth=depth, threshold=threshold)


def _maybe_emit_cleared(depth: int) -> None:
    threshold = _queue_depth_threshold()
    if threshold is None or depth > threshold:
        return
    emit_write_queue_cleared(depth=depth, threshold=threshold)


def enqueue_and_wait() -> threading.Event:
    """Enqueue this thread's event, wait without timeout, return the event."""
    my_event = threading.Event()
    state = _state()
    state.event = my_event
    with _mutex:
        _deque.append(my_event)
        is_head = _deque[0] is my_event
        depth = len(_deque)
    _maybe_emit_armed(depth)
    if is_head:
        my_event.set()
    else:
        my_event.wait()
    return my_event


def cancel_wait(my_event: threading.Event) -> None:
    """Remove a waiter that failed before finishing acquire; wake the next head."""
    depth_after = 0
    with _mutex:
        if _deque and _deque[0] is my_event:
            _deque.popleft()
            if _deque:
                _deque[0].set()
        else:
            try:
                _deque.remove(my_event)
            except ValueError:
                pass
        depth_after = len(_deque)
    _maybe_emit_cleared(depth_after)
    st = _state()
    if st.event is my_event:
        st.event = None


def release_ticket(my_event: threading.Event) -> None:
    """Pop the head after a completed write; wake the next waiter."""
    depth_after = 0
    with _mutex:
        if _deque and _deque[0] is my_event:
            _deque.popleft()
            if _deque:
                _deque[0].set()
        depth_after = len(_deque)
    _maybe_emit_cleared(depth_after)
    st = _state()
    st.event = None


def enter_nested() -> None:
    """Record a nested ``write_connect`` on the same thread."""
    _state().depth += 1


def leave_nested() -> None:
    """Leave a nested ``write_connect`` without releasing the ticket."""
    _state().depth -= 1


def outer_depth() -> int:
    """Current nesting depth (0 when no write ticket is held)."""
    return _state().depth


def set_outer_connection(conn: object) -> None:
    """Bind the connection for the outermost write ticket on this thread."""
    _state().conn = conn


def clear_outer_connection() -> None:
    """Clear the thread-local connection after close."""
    _state().conn = None


def outer_connection() -> object | None:
    """Connection held by the outermost write ticket, if any."""
    return _state().conn


__all__ = [
    "WriteTicketState",
    "cancel_wait",
    "clear_outer_connection",
    "enqueue_and_wait",
    "enter_nested",
    "leave_nested",
    "outer_connection",
    "outer_depth",
    "release_ticket",
    "set_outer_connection",
]
