"""Event factories + minimal UDS publisher for grokbuild-worker.

Lifecycle signals (``^[a-z]+(\\.[a-z]+){1,4}$``):
* ``grokbuild.worker.started``  — emitted at end of lifespan startup
* ``grokbuild.worker.stopped``  — emitted at lifespan shutdown
* ``grokbuild.worker.degraded`` — emitted when any startup check fails

Sync-endpoint signals:
* ``grokbuild.models.listed``      — GET /models completed
* ``grokbuild.worktree.created``   — POST /worktrees completed/rejected
* ``grokbuild.worktree.listed``    — GET /worktrees completed
* ``grokbuild.worktree.removed``   — DELETE /worktrees/{name} completed/rejected
* ``grokbuild.push.completed``     — POST /worktrees/{name}/push completed/rejected
* ``grokbuild.pr.created``         — POST /worktrees/{name}/pull-requests
* ``grokbuild.dispatch.fetched``   — GET /dispatches/{id}/result

``publish_nowait`` submits UDS I/O to the default thread-pool executor
so route handlers are never blocked by event-service latency.

We deliberately do *not* import :mod:`grokbuild.events_core` (which pulls
in the mcp-server-local ``mcp_events`` recorder). The worker is a
separate process with no mcp-server dependency. Instead we publish to
the event service UDS directly, falling back silently if the socket is
unavailable — the worker must never fail to boot because the event
service is down.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from datetime import UTC, datetime
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

_EVENTS_SOCK = os.getenv("EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock")
_SEND_TIMEOUT = 1.0


@event_factory
def GrokbuildWorkerStarted(  # noqa: N802
    version: str,
    deploy_shape: str,
    port: int,
    degraded_checks: list[str],
) -> Event:
    return Event(
        signal="grokbuild.worker.started",
        payload={
            "version": version,
            "deploy_shape": deploy_shape,
            "port": port,
            "degraded_checks": degraded_checks,
        },
        scope="global",
    )


@event_factory
def GrokbuildWorkerStopped(  # noqa: N802
    reason: str,
    uptime_s: float,
) -> Event:
    return Event(
        signal="grokbuild.worker.stopped",
        payload={"reason": reason, "uptime_s": uptime_s},
        scope="global",
    )


@event_factory
def GrokbuildWorkerDegraded(  # noqa: N802
    failing_checks: list[str],
) -> Event:
    return Event(
        signal="grokbuild.worker.degraded",
        payload={"failing_checks": failing_checks},
        scope="global",
    )


def publish_lib_signal(signal: str, payload: dict[str, Any]) -> None:
    """Relay ``mcp.grokbuild.*`` lib events from the worker process."""
    _emit_uds(Event(signal=signal, payload=payload, scope="global"))


def _emit_uds(event: Event) -> None:
    """Best-effort NDJSON publish over the event-service UDS.

    Fire-and-forget: socket errors are logged at DEBUG and swallowed so
    worker boot/shutdown never blocks on event-service availability.
    """
    if not os.path.exists(_EVENTS_SOCK):
        logger.debug("event service socket missing: %s", _EVENTS_SOCK)
        return
    payload: dict[str, Any] = {
        "signal": event.signal,
        "payload": event.payload,
        "scope": event.scope,
        "timestamp": datetime.now(UTC).isoformat(),
        "source": "grokbuild-worker",
    }
    line = (json.dumps(payload) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(_SEND_TIMEOUT)
        sock.connect(_EVENTS_SOCK)
        sock.sendall(line)
    except OSError as exc:
        logger.debug("event publish failed: %s", exc)
    finally:
        sock.close()


def emit_started(
    version: str, deploy_shape: str, port: int, degraded_checks: list[str]
) -> None:
    _emit_uds(GrokbuildWorkerStarted(version, deploy_shape, port, degraded_checks))


def emit_stopped(reason: str, uptime_s: float) -> None:
    _emit_uds(GrokbuildWorkerStopped(reason, uptime_s))


def emit_degraded(failing_checks: list[str]) -> None:
    _emit_uds(GrokbuildWorkerDegraded(failing_checks))


def publish_nowait(event: Event) -> None:
    """Submit a UDS event publish to the thread-pool without blocking the caller.

    Must be called from within a running asyncio event loop (i.e. inside an
    async route handler). The returned Future is intentionally not awaited —
    fire-and-forget semantics.
    """
    asyncio.get_running_loop().run_in_executor(None, _emit_uds, event)


# ---------------------------------------------------------------------------
# Sync-endpoint event factories
# ---------------------------------------------------------------------------


@event_factory
def GrokbuildModelsListed(count: int, duration_s: float) -> Event:  # noqa: N802
    return Event(
        signal="grokbuild.models.listed",
        payload={"count": count, "duration_s": duration_s},
        scope="global",
    )


@event_factory
def GrokbuildWorktreeCreated(  # noqa: N802
    name: str, branch: str, duration_s: float, outcome: str
) -> Event:
    return Event(
        signal="grokbuild.worktree.created",
        payload={
            "name": name,
            "branch": branch,
            "duration_s": duration_s,
            "outcome": outcome,
        },
        scope="global",
    )


@event_factory
def GrokbuildWorktreeListed(count: int, duration_s: float) -> Event:  # noqa: N802
    return Event(
        signal="grokbuild.worktree.listed",
        payload={"count": count, "duration_s": duration_s},
        scope="global",
    )


@event_factory
def GrokbuildWorktreeRemoved(  # noqa: N802
    name: str, duration_s: float, outcome: str
) -> Event:
    return Event(
        signal="grokbuild.worktree.removed",
        payload={"name": name, "duration_s": duration_s, "outcome": outcome},
        scope="global",
    )


@event_factory
def GrokbuildPushCompleted(  # noqa: N802
    name: str, branch: str, duration_s: float, outcome: str, commits_pushed: int
) -> Event:
    return Event(
        signal="grokbuild.push.completed",
        payload={
            "name": name,
            "branch": branch,
            "duration_s": duration_s,
            "outcome": outcome,
            "commits_pushed": commits_pushed,
        },
        scope="global",
    )


@event_factory
def GrokbuildPRCreated(  # noqa: N802
    name: str, pr_number: int | None, duration_s: float, outcome: str
) -> Event:
    return Event(
        signal="grokbuild.pr.created",
        payload={
            "name": name,
            "pr_number": pr_number,
            "duration_s": duration_s,
            "outcome": outcome,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchFetched(  # noqa: N802
    dispatch_id: str, outcome: str, duration_s: float, result_size_bytes: int
) -> Event:
    return Event(
        signal="grokbuild.dispatch.fetched",
        payload={
            "dispatch_id": dispatch_id,
            "outcome": outcome,
            "duration_s": duration_s,
            "result_size_bytes": result_size_bytes,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchAcceptedEvent(  # noqa: N802
    dispatch_id: str, model: str, worktree: str, requested_by: str
) -> Event:
    return Event(
        signal="grokbuild.dispatch.accepted",
        payload={
            "dispatch_id": dispatch_id,
            "model": model,
            "worktree": worktree,
            "requested_by": requested_by,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchStarted(  # noqa: N802
    dispatch_id: str, pid: int, model: str
) -> Event:
    return Event(
        signal="grokbuild.dispatch.started",
        payload={"dispatch_id": dispatch_id, "pid": pid, "model": model},
        scope="global",
    )


@event_factory
def GrokbuildDispatchProgress(  # noqa: N802
    dispatch_id: str, event_type: str, summary: str
) -> Event:
    return Event(
        signal="grokbuild.dispatch.progress",
        payload={
            "dispatch_id": dispatch_id,
            "event_type": event_type,
            "summary": summary,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchCompleted(  # noqa: N802
    dispatch_id: str, outcome: str, duration_s: float, exit_code: int | None
) -> Event:
    return Event(
        signal="grokbuild.dispatch.completed",
        payload={
            "dispatch_id": dispatch_id,
            "outcome": outcome,
            "duration_s": duration_s,
            "exit_code": exit_code,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchCancelledEvent(  # noqa: N802
    dispatch_id: str, reason: str, signal_used: str
) -> Event:
    return Event(
        signal="grokbuild.dispatch.cancelled",
        payload={
            "dispatch_id": dispatch_id,
            "reason": reason,
            "signal_used": signal_used,
        },
        scope="global",
    )


@event_factory
def GrokbuildDispatchRejectedEvent(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    running: int = 0,
    capacity: int = 0,
) -> Event:
    """Worker-side admission rejection event (admission-phase contract).

    Fires when the worker rejects a dispatch BEFORE the runner spawns —
    chiefly capacity exhaustion (``reason_code=capacity_exhausted``),
    where ``running`` / ``capacity`` carry the tracker state at rejection
    time. Carries its own correlation fields per
    ``[universal:events]:admission-phase-payload-contract`` — no
    ``.started`` join required because none will fire on this path.
    """
    return Event(
        signal="grokbuild.dispatch.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "running": running,
            "capacity": capacity,
        },
        scope="global",
    )


@event_factory
def GrokbuildTrackerOrphanCleaned(  # noqa: N802
    count: int, dispatch_ids: list[str]
) -> Event:
    return Event(
        signal="grokbuild.tracker.orphan.cleaned",
        payload={"count": count, "dispatch_ids": dispatch_ids},
        scope="global",
    )


def envelope_outcome(envelope: dict[str, Any]) -> str:
    """Map envelope status → outcome label for event payloads.

    The ``"cancelled"`` branch covers tracker-side terminal states fed
    through this helper from the runner; ``"failed"`` envelopes whose
    metadata reason_code names a timeout return ``"timeout"`` so SSE
    subscribers can distinguish that path from generic external failures.
    """
    status = envelope.get("status", "")
    if status == "completed":
        return "success"
    if status == "cancelled":
        return "cancelled"
    if status == "rejected":
        return "client_error"
    if status == "failed":
        meta = envelope.get("metadata", {})
        if "timeout" in meta.get("reason_code", ""):
            return "timeout"
        return "external_failure"
    return "server_error"
