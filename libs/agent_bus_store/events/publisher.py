"""Background-thread UDS event publisher for agent-bus.

Follows the same wire format as mcp_events.py so the event service receives
agent-bus lifecycle signals alongside all other service signals.

∀ emit call: fire-and-forget; drops oldest if queue is full, never blocks the caller.
Falls back silently if the event service socket is unavailable.
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
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line)
            except queue.Full:
                pass

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
                sock.sendall(line.encode())
            except queue.Empty:
                continue
            except OSError:
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
