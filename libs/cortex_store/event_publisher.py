"""Standalone UDS event publisher for cortex-api.

Follows the same NDJSON wire format as mcp_events.py so the event service
receives cortex-api lifecycle signals (e.g. mcp.session.close.atomic,
mcp.session.close.rejected) without requiring the mcp-server
``request_profile`` dependency.

∀ emit call: fire-and-forget; drops oldest if queue is full, never blocks
the caller. Falls back silently if the event service socket is unavailable.

∀ signal: stdlib + universal_logging only — no mcp-server imports allowed.
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
_ENABLED = os.getenv("CORTEX_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")
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
            target=self._run, daemon=True, name="cortex-api-events-uds"
        )
        self._thread.start()

    def put_nowait(self, line: str) -> None:
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
                    logger.warning("cortex-api event publisher connect error: %s", e)
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
                except OSError as close_error:
                    logger.warning(
                        "cortex-api event publisher socket close failed: %s",
                        close_error,
                    )
                sock = None
                time.sleep(_RECONNECT_DELAY)


_publisher: _UDSPublisher | None = _UDSPublisher(_EVENTS_SOCK) if _ENABLED else None


def record(signal: str, **payload: Any) -> None:
    """Publish a structured event to the event service via UDS.

    Wire format mirrors mcp_events.py — source is ``cortex-api`` here since
    cortex_store runs inside the cortex-api process, not the mcp-server.
    """
    if _publisher is None:
        return
    now = datetime.now(UTC)
    event: dict[str, Any] = {
        "signal": signal,
        "source": "cortex-api",
        "role": "observation",
        "scope": "global",
        "timestamp": now.isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": payload,
    }
    _publisher.put_nowait(json.dumps(event, default=str) + "\n")
