"""Emit debug events directly to the event service UDS socket.

Bypasses the Stargate event bus — writes directly to the event service
ingest socket. For temporary diagnostic instrumentation only.

Debug events (role='debug') are pruned at session boundary by the event
service retention loop. They are queryable via raw_sql but excluded from
business-metric operations (recent-failures, noise-profile, etc.).

Silent on failure: diagnostic events must never affect pipeline correctness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_EVENTS_SOCK = os.environ.get(
    "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
)


async def emit_debug_event(
    signal: str,
    payload: dict[str, Any],
    source: str = "pipeline",
    *,
    role: str = "debug",
    scope: str = "node",
) -> None:
    """Publish an event to the event service UDS ingest socket.

    Writes directly using NDJSON. No-op if the socket is unreachable — never raises.

    Args:
        signal: Dot-notation signal name (e.g. 'pipeline.debug.validate')
        payload: Event payload dict
        source: Originating service identifier
        role: Event role; default ``debug`` (session-pruned diagnostics).
        scope: ``node`` or ``global``.
    """
    now = datetime.now(UTC)
    event: dict[str, Any] = {
        "signal": signal,
        "source": source,
        "role": role,
        "scope": scope,
        "timestamp": now.isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": payload,
    }
    line = json.dumps(event, default=str) + "\n"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(_EVENTS_SOCK),
            timeout=2.0,
        )
        writer.write(line.encode())
        await asyncio.wait_for(writer.drain(), timeout=2.0)
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
