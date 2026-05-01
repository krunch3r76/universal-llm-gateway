"""Emit observation-role events to the Event Service ingest socket (NDJSON over UDS).

Used by manage TUI / topology deploy flows for build lifecycle visibility.
Silent on failure — never raises.
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


# ---------------------------------------------------------------------------
# Fleet lifecycle events
# ---------------------------------------------------------------------------


async def emit_fleet_operation_started(
    *, operation: str, build: bool, scope: str, remotes: list[str]
) -> None:
    """Emitted when Sync+Restart All or Rebuild+Deploy All begins."""
    await _emit(
        "fleet.operation.started",
        {
            "operation": operation,
            "build": build,
            "scope": scope,
            "remotes": remotes,
        },
    )


async def emit_fleet_operation_completed(
    *,
    operation: str,
    build: bool,
    success: bool,
    duration_s: float,
    failures: list[str],
) -> None:
    await _emit(
        "fleet.operation.completed",
        {
            "operation": operation,
            "build": build,
            "success": success,
            "duration_s": round(duration_s, 3),
            "failures": failures,
        },
    )


async def emit_fleet_service_phase(
    *, phase: str, services: list[str], results: dict[str, bool]
) -> None:
    """Emitted after each restart phase (stop, start-critical, start-optional)."""
    await _emit(
        "fleet.service.phase",
        {"phase": phase, "services": services, "results": results},
    )


async def emit_fleet_relay_status(
    *, hostname: str, connected: bool, duration_s: float
) -> None:
    await _emit(
        "fleet.relay.status",
        {
            "hostname": hostname,
            "connected": connected,
            "duration_s": round(duration_s, 3),
        },
    )


# ---------------------------------------------------------------------------
# Sync emitter (for non-async callers, e.g. service_config recovery path)
# ---------------------------------------------------------------------------


def _emit_sync(
    signal: str,
    payload: dict[str, Any],
    *,
    source: str = "manage",
    role: str = "observation",
    scope: str = "node",
) -> None:
    """Blocking UDS emit — mirrors _emit for sync callers. Silent on failure."""
    import socket

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
    line = (json.dumps(event, default=str) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(_EVENTS_SOCK)
            sock.sendall(line)
    except Exception:
        logger.debug("observation_event sync emit failed for %s", signal, exc_info=True)


def emit_relay_socket_recovery(
    *, socket_dir: str, owner_uid: int, recovered: bool
) -> None:
    """Emitted each time _recover_root_owned_socket_dir activates.

    Lets operators confirm this path trends to zero after Fix 1 lands.
    Signal: relay.socket.recovery
    """
    _emit_sync(
        "relay.socket.recovery",
        {"socket_dir": socket_dir, "owner_uid": owner_uid, "recovered": recovered},
    )
