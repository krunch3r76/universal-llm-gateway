"""Structured event recorder for MCP server.

Publishes events to the event service over UDS
(/tmp/universal-protocol/events.sock) using the same NDJSON wire format
as all other services (Stargate, RAG, cloud proxy).

Falls back silently if the event service socket is unavailable so the MCP
server starts cleanly even when the event service is down.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_SOCK", "/tmp/universal-protocol/events.sock")
_ENABLED = os.getenv("MCP_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")
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
        self._dropped = 0
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="mcp-events-uds"
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
            self._dropped += 1

    def _run(self) -> None:
        sock: socket.socket | None = None
        while True:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(_SEND_TIMEOUT)
                    sock.connect(self._sock_path)
                except OSError as e:
                    logger.warning("UDS publisher connection/send error: %s", e)
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


def record(signal: str, **payload: Any) -> None:
    """Publish a structured event to the event service.

    Signals follow dotted convention: mcp.{domain}.{action}
    """
    if _publisher is None:
        return

    event: dict[str, Any] = {
        "signal": signal,
        "source": "mcp-server",
        "role": "observation",
        "scope": "global",
        "timestamp": (now := datetime.now(UTC)).isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": payload,
    }
    _publisher.put_nowait(json.dumps(event, default=str) + "\n")


def monotonic_now() -> float:
    """Return current monotonic time for duration calculations."""
    return time.monotonic()
