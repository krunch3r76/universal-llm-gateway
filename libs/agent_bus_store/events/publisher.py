"""Background-thread UDS event publisher for agent-bus.

Follows the same wire format as mcp_events.py so the event service receives
agent-bus lifecycle signals alongside all other service signals.

∀ emit call: fire-and-forget; drops oldest if queue is full, never blocks the caller.
Falls back silently if the event service socket is unavailable.

Drop-oldest on a full queue and post-dequeue ``sendall`` loss each increment a
process-local counter (``dropped_enqueue``, ``dropped_send``) and emit a
warning that names the lost signal. Neither path requeues; ``emit()`` stays
fire-and-forget. Counters reset with the process — they are a live discriminator,
not an Event Service signal.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock")
_ENABLED = os.getenv("AGENT_BUS_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")
_QUEUE_MAX = 500
_RECONNECT_DELAY = 5.0
_SEND_TIMEOUT = 2.0

dropped_enqueue = 0
dropped_send = 0


def snapshot_drop_counters() -> dict[str, int]:
    """Return process-local drop counters for ``GET /health``.

    Both keys are always present. Zero means no drop since this process
    started, not that losses are unobservable. Counters reset on restart.
    """
    return {
        "dropped_enqueue": dropped_enqueue,
        "dropped_send": dropped_send,
    }


def _signal_from_line(line: str) -> str:
    """Return the event ``signal`` from an NDJSON line, or a fallback token."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return "<unparseable>"
    if isinstance(obj, dict):
        sig = obj.get("signal")
        if isinstance(sig, str) and sig:
            return sig
    return "<unknown>"


def _note_enqueue_drop(line: str) -> None:
    """Count + warn a drop-oldest (or failed re-put) enqueue loss."""
    global dropped_enqueue
    dropped_enqueue += 1
    logger.warning(
        "Agent-bus event publisher drop-oldest (enqueue): signal=%s dropped_enqueue=%d",
        _signal_from_line(line),
        dropped_enqueue,
    )


def _note_send_drop(line: str) -> None:
    """Count + warn a post-dequeue sendall loss (line is not requeued)."""
    global dropped_send
    dropped_send += 1
    logger.warning(
        "Agent-bus event publisher send loss after dequeue: signal=%s dropped_send=%d",
        _signal_from_line(line),
        dropped_send,
    )


class _UDSPublisher:
    """Thread-based UDS publisher with bounded queue and auto-reconnect.

    ∀ event: either delivered or dropped (never blocks the caller).
    Drop policy: drop-oldest when queue full.
    """

    def __init__(self, sock_path: str) -> None:
        self._sock_path = sock_path
        self._q: queue.Queue[str] = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="agent-bus-events-uds"
        )
        self._thread.start()

    def put_nowait(self, line: str) -> None:
        """Enqueue *line* for delivery; drop oldest if full."""
        try:
            self._q.put_nowait(line)
        except queue.Full:
            dropped: str | None = None
            try:
                dropped = self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line)
            except queue.Full:
                _note_enqueue_drop(line)
                if dropped is not None:
                    _note_enqueue_drop(dropped)
                return
            if dropped is not None:
                _note_enqueue_drop(dropped)

    def _run(self) -> None:
        sock: socket.socket | None = None
        while True:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(_SEND_TIMEOUT)
                    sock.connect(self._sock_path)
                except OSError as e:
                    logger.warning("Agent-bus event publisher connect error: %s", e)
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
                    time.sleep(_RECONNECT_DELAY)
                    continue
            try:
                line = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                sock.sendall(line.encode())
            except OSError:
                _note_send_drop(line)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                time.sleep(_RECONNECT_DELAY)


_publisher: _UDSPublisher | None = _UDSPublisher(_EVENTS_SOCK) if _ENABLED else None


def emit(signal: str, payload: dict[str, Any], *, role: str = "observation") -> None:
    """Publish a structured lifecycle event to the event service via UDS."""
    if _publisher is None:
        return
    ts = datetime.now(UTC)
    event: dict[str, Any] = {
        "signal": signal,
        "source": "agent-bus",
        "role": role,
        "scope": "global",
        "timestamp": ts.isoformat(),
        "ts_unix_ms": int(ts.timestamp() * 1000),
        "payload": payload,
    }
    _publisher.put_nowait(json.dumps(event, default=str) + "\n")
