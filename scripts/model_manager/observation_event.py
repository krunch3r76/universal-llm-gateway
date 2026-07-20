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


async def emit_fleet_service_step(
    *, phase: str, service: str, success: bool, duration_s: float
) -> None:
    """Per-service timing for each stop/start step within a fleet operation.

    Emitted after every individual service operation so bottlenecks can be
    identified by querying ``fleet.service.step`` events grouped by service.
    """
    await _emit(
        "fleet.service.step",
        {
            "phase": phase,
            "service": service,
            "success": success,
            "duration_s": round(duration_s, 3),
        },
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


# ---------------------------------------------------------------------------
# Deferred restart-intent lifecycle events (git-worker event-driven drain, P2)
# ---------------------------------------------------------------------------
# Follow this module's UDS observation-emit pattern (top-level ``signal`` field,
# which the event-service WS ``/v1/subscribe`` filter keys on), colocated with
# the fleet emitters above. Signal names satisfy the lib signal regex
# (^[a-z]+(\.[a-z]+){1,4}$); scope=node.


async def emit_manage_restart_deferred(
    *, intent_id: str, service: str, drain_epoch: int | None, deadline_at: str
) -> None:
    """A non-force restart was deferred; the worker is draining (202 path)."""
    await _emit(
        "manage.restart.deferred",
        {
            "intent_id": intent_id,
            "service": service,
            "drain_epoch": drain_epoch,
            "deadline_at": deadline_at,
        },
    )


async def emit_manage_restart_draining(
    *,
    intent_id: str,
    service: str,
    elapsed_s: float,
    active_count: int,
    active_ops: list[dict[str, Any]],
) -> None:
    """Periodic drain-progress heartbeat — observability-first (§3.2 step 5).

    Carries the live active-ops snapshot so a wedged/slow drain is visible and
    actionable well before any deadline; the deadline is a last resort, never the
    mechanism.
    """
    await _emit(
        "manage.restart.draining",
        {
            "intent_id": intent_id,
            "service": service,
            "elapsed_s": round(elapsed_s, 1),
            "active_count": active_count,
            "active_ops": active_ops,
        },
    )


async def emit_manage_restart_drain_completed(
    *, intent_id: str, drain_epoch: int, worker_id: str | None
) -> None:
    """The worker converged to idle for this intent's epoch (pre-SIGTERM)."""
    await _emit(
        "manage.restart.drain_completed",
        {
            "intent_id": intent_id,
            "drain_epoch": drain_epoch,
            "worker_id": worker_id,
        },
    )


async def emit_manage_restart_completed(
    *, intent_id: str, duration_s: float
) -> None:
    """The deferred restart finished (SIGTERM delivered, intent completed)."""
    await _emit(
        "manage.restart.completed",
        {"intent_id": intent_id, "duration_s": round(duration_s, 1)},
    )


async def emit_manage_restart_failed(*, intent_id: str, reason: str) -> None:
    """The deferred restart could not complete (kill error / abort)."""
    await _emit(
        "manage.restart.failed",
        {"intent_id": intent_id, "reason": reason},
    )


async def emit_manage_restart_timeout(
    *,
    intent_id: str,
    service: str,
    deadline_at: str | None,
    stuck_ops: list[dict[str, Any]],
    affordances: list[str],
) -> None:
    """Alert-only terminal: the deadline passed before convergence (R-F).

    NEVER an auto-SIGKILL — the supervisor stops and surfaces the stuck-op
    identity + the explicit-force affordance for an operator to act.
    """
    await _emit(
        "manage.restart.timeout",
        {
            "intent_id": intent_id,
            "service": service,
            "deadline_at": deadline_at,
            "stuck_ops": stuck_ops,
            "affordances": affordances,
        },
    )


# ---------------------------------------------------------------------------
# Operator restart-window visibility (friction 24630)
# ---------------------------------------------------------------------------


async def emit_manage_restart_window_opened(
    *,
    window_id: str,
    scope: str,
    service_set: list[str],
    deadline_at: str,
    reason: str,
) -> None:
    """Operator-authored restart window opened — MUST precede the first stop."""
    await _emit(
        "manage.restart.window.opened",
        {
            "window_id": window_id,
            "scope": scope,
            "service_set": service_set,
            "deadline_at": deadline_at,
            "reason": reason,
        },
    )


async def emit_manage_restart_window_cleared(
    *,
    window_id: str,
    scope: str,
    service_set: list[str],
    reason: str,
) -> None:
    """Restart window cleared (healthy, fleet completed, or TTL sweep)."""
    await _emit(
        "manage.restart.window.cleared",
        {
            "window_id": window_id,
            "scope": scope,
            "service_set": service_set,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# Manage-owned digest tick loop (todo:digest-manage-tick-loop)
# ---------------------------------------------------------------------------


async def emit_manage_digest_tick_started() -> None:
    """Periodic digest ticker started with manage lifecycle."""
    await _emit("manage.digest.tick.started", {})


async def emit_manage_digest_tick_stopped() -> None:
    """Periodic digest ticker stopped; in-flight to_thread tick drained."""
    await _emit("manage.digest.tick.stopped", {})


async def emit_manage_digest_tick_skipped(*, reason: str) -> None:
    """Tick skipped (cortex-api unhealthy, wrong extract backend, etc.)."""
    await _emit("manage.digest.tick.skipped", {"reason": reason})


async def emit_manage_digest_tick_error(*, reason: str) -> None:
    """Non-fatal tick failure; loop continues."""
    await _emit("manage.digest.tick.error", {"reason": reason})


async def emit_manage_digest_tick_completed(*, count: int, status: str) -> None:
    """Tick advanced at least one job."""
    await _emit("manage.digest.tick.completed", {"count": count, "status": status})


async def emit_manage_charter_tick_started() -> None:
    """Charter-runner tick started with manage lifecycle."""
    await _emit("manage.charter.tick.started", {})


async def emit_manage_charter_tick_stopped() -> None:
    """Charter-runner tick stopped with manage lifecycle."""
    await _emit("manage.charter.tick.stopped", {})


async def emit_manage_charter_tick_scanned(*, roots: int, admitted: int) -> None:
    """One scan pass over enrolled roots completed."""
    await _emit("manage.charter.tick.scanned", {"roots": roots, "admitted": admitted})


async def emit_manage_charter_tick_admitted(
    *, root: str, dispatch_id: str, worker_thread: str
) -> None:
    """A fresh windowed cursor-sdk continuation was admitted for a root."""
    await _emit(
        "manage.charter.tick.admitted",
        {"root": root, "dispatch_id": dispatch_id, "worker_thread": worker_thread},
    )


async def emit_manage_charter_tick_window_failed(*, root: str, reason: str) -> None:
    """A window failed/stalled; root is stopped pending human re-arm (no auto-retry)."""
    await _emit("manage.charter.tick.window_failed", {"root": root, "reason": reason})


async def emit_manage_charter_tick_waiting_open(*, root: str, age_s: int) -> None:
    """Attended handoff still waiting for operator to open the IDE thread."""
    await _emit(
        "manage.charter.tick.waiting_open", {"root": root, "age_s": age_s}
    )


async def emit_manage_charter_tick_error(*, reason: str) -> None:
    """Non-fatal charter tick failure; loop continues."""
    await _emit("manage.charter.tick.error", {"reason": reason})


async def emit_manage_charter_tick_reloaded(*, modules: list[str]) -> None:
    """Charter-runner modules reloaded in-process; tick loop restarted."""
    await _emit(
        "manage.charter.tick.reloaded",
        {"modules": modules, "count": len(modules)},
    )
