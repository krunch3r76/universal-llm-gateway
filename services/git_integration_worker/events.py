"""Minimal UDS publisher hook for ``libs/git_integrate`` events."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock")
_SEND_TIMEOUT = 1.0


def publish_lib_signal(signal: str, payload: dict[str, Any]) -> None:
    """Publish one lib event to the event-service ingest socket (best-effort)."""
    try:
        body = json.dumps(
            {
                "signal": signal,
                "source": "git_integration_worker",
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        ).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_SEND_TIMEOUT)
            sock.connect(_EVENTS_SOCK)
            sock.sendall(body)
    except OSError as exc:
        logger.debug("git_integrate event publish skipped: %s", exc)
