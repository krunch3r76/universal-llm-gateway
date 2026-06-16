"""Event factories for git-integration-worker cooperative drain signals.

`@event_factory`-declared per `[universal:events]` (signal vocabulary lives with
the factory, not as ad-hoc ``publish_lib_signal`` string literals), emitted
through the same UDS publisher seam as the cursor-sdk worker signals
(``register_*_publisher`` wired in the app lifespan). Mirrors
``cursor_sdk_events.py``; the drain signals parallel ``mcp.maintenance.drain.*``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_git_worker_drain_event_publisher(
    publisher: Callable[[str, dict[str, Any]], None],
) -> None:
    """Register the UDS sink (``events.publish_lib_signal``) used as fallback."""
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


@event_factory
def GitWorkerDrainStarted(  # noqa: N802
    reason: str,
    intent_id: str,
    drain_epoch: int,
    worker_id: str,
    pid: int,
    worker_started_at: str,
    active_count: int,
    active_ops: list[dict[str, Any]],
) -> Event:
    return Event(
        signal="git_worker.drain.started",
        payload={
            "reason": reason,
            "intent_id": intent_id,
            "drain_epoch": drain_epoch,
            "worker_id": worker_id,
            "pid": pid,
            "worker_started_at": worker_started_at,
            "active_count": active_count,
            "active_ops": active_ops,
        },
        scope="node",
    )


@event_factory
def GitWorkerDrainCompleted(  # noqa: N802
    intent_id: str,
    drain_epoch: int,
    worker_id: str,
    pid: int,
    completed_at: str,
    active_count: int,
) -> Event:
    return Event(
        signal="git_worker.drain.completed",
        payload={
            "intent_id": intent_id,
            "drain_epoch": drain_epoch,
            "worker_id": worker_id,
            "pid": pid,
            "completed_at": completed_at,
            "active_count": active_count,
        },
        scope="node",
    )


@event_factory
def GitWorkerAdmissionRejected(  # noqa: N802
    kind: str,
    route: str,
    intent_id: str | None,
    drain_epoch: int,
) -> Event:
    return Event(
        signal="git_worker.admission.rejected",
        payload={
            "kind": kind,
            "route": route,
            "intent_id": intent_id,
            "drain_epoch": drain_epoch,
        },
        scope="node",
    )


def emit_drain_started(
    *,
    reason: str,
    intent_id: str,
    drain_epoch: int,
    worker_id: str,
    pid: int,
    worker_started_at: str,
    active_count: int,
    active_ops: list[dict[str, Any]],
) -> None:
    _emit(
        GitWorkerDrainStarted(
            reason=reason,
            intent_id=intent_id,
            drain_epoch=drain_epoch,
            worker_id=worker_id,
            pid=pid,
            worker_started_at=worker_started_at,
            active_count=active_count,
            active_ops=active_ops,
        )
    )
    logger.info(
        "git-worker drain started: intent_id=%s epoch=%d reason=%s active=%d",
        intent_id,
        drain_epoch,
        reason,
        active_count,
    )


def emit_drain_completed(
    *,
    intent_id: str | None,
    drain_epoch: int,
    worker_id: str,
    pid: int,
    completed_at: str,
    active_count: int,
) -> None:
    _emit(
        GitWorkerDrainCompleted(
            intent_id=intent_id or "",
            drain_epoch=drain_epoch,
            worker_id=worker_id,
            pid=pid,
            completed_at=completed_at,
            active_count=active_count,
        )
    )
    logger.info(
        "git-worker drain completed: intent_id=%s epoch=%d worker_id=%s",
        intent_id,
        drain_epoch,
        worker_id,
    )


def emit_admission_rejected(
    *,
    kind: str,
    route: str,
    intent_id: str | None,
    drain_epoch: int,
) -> None:
    _emit(
        GitWorkerAdmissionRejected(
            kind=kind,
            route=route,
            intent_id=intent_id,
            drain_epoch=drain_epoch,
        )
    )
