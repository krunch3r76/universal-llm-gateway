"""Emit observation-role events to the Event Service ingest socket (NDJSON over UDS).

Used by manage TUI / topology deploy flows for build lifecycle visibility.
Silent on failure — never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_EVENTS_SOCK = "/tmp/universal-protocol/events.sock"


async def _emit(
    signal: str,
    payload: dict[str, Any],
    *,
    source: str = "manage",
    role: str = "observation",
    scope: str = "node",
) -> None:
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
        _, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(_EVENTS_SOCK),
            timeout=2.0,
        )
        writer.write(line.encode())
        await asyncio.wait_for(writer.drain(), timeout=2.0)
        writer.close()
        await writer.wait_closed()
    except Exception:
        logger.debug("observation_event emit failed for %s", signal, exc_info=True)


async def emit_build_image_started(*, host: str, scope: str) -> None:
    await _emit("build.image.started", {"host": host, "scope": scope})


async def emit_build_image_completed(
    *,
    host: str,
    scope: str,
    success: bool,
    duration_s: float,
) -> None:
    await _emit(
        "build.image.completed",
        {
            "host": host,
            "scope": scope,
            "success": success,
            "duration_s": round(duration_s, 3),
        },
    )


async def emit_build_image_mismatch(
    *,
    host: str,
    mismatched_fields: list[str],
    local_labels: dict[str, str],
    remote_labels: dict[str, str],
) -> None:
    await _emit(
        "build.image.mismatch",
        {
            "host": host,
            "mismatched_fields": mismatched_fields,
            "local_labels": local_labels,
            "remote_labels": remote_labels,
        },
    )
