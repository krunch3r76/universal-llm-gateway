"""Structured event recorder for MCP server.

Publishes events to the event service over UDS
(/tmp/universal-protocol/events.sock) using the same NDJSON wire format
as all other services (Stargate, RAG, cloud proxy).

Falls back silently if the event service socket is unavailable so the MCP
server starts cleanly even when the event service is down.
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

from request_profile import current_request_metadata
from universal_logging import get_logger

logger = get_logger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock")
_ENABLED = os.getenv("MCP_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")
_QUEUE_MAX = 500
_RECONNECT_DELAY = 5.0
_SEND_TIMEOUT = 2.0
_FLUSH_TIMEOUT = 2.0


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
                # Balance the unfinished-task count for the dropped line so
                # flush()'s join semantics stay accurate.
                self._q.task_done()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(line)
            except queue.Full:
                pass
            self._dropped += 1

    def flush(self, timeout_s: float = _FLUSH_TIMEOUT) -> bool:
        """Block until the queue drains or *timeout_s* elapses.

        Returns True when every enqueued line has been handed to the worker
        (queue fully drained), False on timeout. A timeout-aware analogue of
        ``queue.Queue.join()``; relies on the worker calling ``task_done()``
        for every dequeued line.
        """
        deadline = time.monotonic() + timeout_s
        with self._q.all_tasks_done:
            while self._q.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._q.all_tasks_done.wait(remaining)
        return True

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
                    except OSError as close_error:
                        logger.warning(
                            "UDS publisher socket close failed during reconnect: %s",
                            close_error,
                        )
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
                try:
                    sock.close()
                except OSError as close_error:
                    logger.warning(
                        "UDS publisher socket close failed after send error: %s",
                        close_error,
                    )
                sock = None
                time.sleep(_RECONNECT_DELAY)
            finally:
                # Mark the dequeued line done regardless of send outcome so
                # flush() can rely on join semantics. A failed send drops the
                # line, preserving prior fire-and-forget behavior.
                self._q.task_done()


_publisher: _UDSPublisher | None = _UDSPublisher(_EVENTS_SOCK) if _ENABLED else None


def record(signal: str, *, role: str = "observation", **payload: Any) -> None:
    """Publish a structured event to the event service.

    ``role`` selects the retention tier (default ``observation`` = session cap).
    Pass ``role="coordination"`` for forensic events that must survive the
    7-day age cap (request lifecycle, security audit). See libs/event_store
    four-tier retention model.

    Signals follow dotted convention: mcp.{domain}.{action}

    Boot signals (emitted by _boot_runner / _boot_manifest):
      mcp.cortex.boot                    — boot completed (agent)
      mcp.cortex.boot.manifest.assembled — manifest built (agent, artifact_count, total_bytes)
      mcp.cortex.boot.fetch.failed       — byte-count serialization failed (error, error_type)

    Request lifecycle events (``mcp.request.*``) may carry a ``surface`` attribute
    (``life`` | ``code``) when served from dual MCP mounts.
    """
    if _publisher is None:
        return

    merged_payload = {**current_request_metadata(), **payload}
    event: dict[str, Any] = {
        "signal": signal,
        "source": "mcp-server",
        "role": role,
        "scope": "global",
        "timestamp": (now := datetime.now(UTC)).isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": merged_payload,
    }
    _publisher.put_nowait(json.dumps(event, default=str) + "\n")


def flush(timeout_s: float = _FLUSH_TIMEOUT) -> bool:
    """Flush pending events before process exit.

    Blocks until the publisher queue drains or *timeout_s* elapses. Returns
    True when fully drained (or when publishing is disabled — nothing to
    flush), False on timeout. Needed on the shutdown path where the daemon
    publisher thread would otherwise die with lines still queued.
    """
    if _publisher is None:
        return True
    return _publisher.flush(timeout_s)


def monotonic_now() -> float:
    """Return current monotonic time for duration calculations."""
    return time.monotonic()
