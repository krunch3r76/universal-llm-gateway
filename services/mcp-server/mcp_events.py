"""Lightweight structured event recorder for MCP server diagnostics.

Writes JSONL to /tmp/mcp-events/current.jsonl inside the container.
Volume-mount /tmp/mcp-events to the host for access without docker exec.

Query examples:
    # All events
    tail -50 /tmp/mcp-events/current.jsonl | jq -c '.'

    # SSE stream drops only
    jq -c 'select(.signal == "mcp.sse.stream.aborted")' /tmp/mcp-events/current.jsonl

    # Session lifecycle
    jq -c 'select(.signal | startswith("mcp.session"))' /tmp/mcp-events/current.jsonl

    # Duration of each SSE stream
    jq -c 'select(.signal | startswith("mcp.sse.stream")) | {signal, duration: .payload.duration_s, reason: .payload.reason}' \
        /tmp/mcp-events/current.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EVENT_DIR = Path(os.getenv("MCP_EVENT_DIR", "/tmp/mcp-events"))
_EVENT_FILE = _EVENT_DIR / "current.jsonl"
_ENABLED = os.getenv("MCP_EVENTS_ENABLED", "true").lower() in ("true", "1", "yes")


def _ensure_dir() -> bool:
    """Create event directory if it doesn't exist. Returns False on failure."""
    try:
        _EVENT_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        logger.warning("Cannot create event directory %s: %s", _EVENT_DIR, exc)
        return False


_dir_ok = _ensure_dir() if _ENABLED else False


def _ensure_dir_ready() -> bool:
    """Ensure event directory is usable, retrying after transient startup failures."""
    global _dir_ok
    if not _ENABLED:
        return False
    if _dir_ok:
        return True
    _dir_ok = _ensure_dir()
    return _dir_ok


def record(signal: str, **payload: Any) -> None:
    """Append a structured event to the JSONL file.

    Signals follow dotted convention: mcp.{domain}.{action}
    """
    if not _ensure_dir_ready():
        return

    event = {
        "signal": signal,
        "timestamp": datetime.now(UTC).isoformat(),
        "ts_mono": time.monotonic(),
        "payload": payload,
    }

    try:
        with _EVENT_FILE.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except OSError as exc:
        logger.warning("Failed to write event %s: %s", signal, exc)


def monotonic_now() -> float:
    """Return current monotonic time for duration calculations."""
    return time.monotonic()
