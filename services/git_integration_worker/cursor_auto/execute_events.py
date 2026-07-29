"""Observation signals for the ``execute`` contract and job-deadline expiry.

Sibling of ``cursor_sdk_events`` (which is already over its module budget); the
publisher wiring is shared via its public ``emit_frontier_event`` hook so these
signals ride the same UDS/mcp_events path as the rest of the Auto lane.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def FrontierSdkAutoExecuteAdmissionBlocked(  # noqa: N802
    thread_id: str,
    reason: str,
    tool_op: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.execute_admission_blocked",
        payload={
            "thread_id": thread_id,
            "reason": reason,
            "tool_op": tool_op,
        },
        scope="node",
    )


def emit_execute_admission_blocked(
    *,
    thread_id: str,
    reason: str,
    tool_op: str | None,
) -> None:
    """Emit when the tier-M manifest refuses an ``execute`` ask."""
    emit_frontier_event(
        FrontierSdkAutoExecuteAdmissionBlocked(
            thread_id=thread_id,
            reason=reason,
            tool_op=tool_op,
        )
    )
    logger.info(
        "cursor-auto execute_admission_blocked: thread_id=%s reason=%s tool_op=%s",
        thread_id,
        reason,
        tool_op,
    )


@event_factory
def FrontierSdkAutoExecuteOpRan(  # noqa: N802
    thread_id: str,
    tool_op: str,
    idempotence: str,
    ok: bool,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.execute_op_ran",
        payload={
            "thread_id": thread_id,
            "tool_op": tool_op,
            "idempotence": idempotence,
            "ok": ok,
        },
        scope="node",
    )


def emit_execute_op_ran(
    *,
    thread_id: str,
    tool_op: str,
    idempotence: str,
    ok: bool,
) -> None:
    """Emit once an in-seat tier-M op returned an observed payload (or failed)."""
    emit_frontier_event(
        FrontierSdkAutoExecuteOpRan(
            thread_id=thread_id,
            tool_op=tool_op,
            idempotence=idempotence,
            ok=ok,
        )
    )
    logger.info(
        "cursor-auto execute_op_ran: thread_id=%s tool_op=%s idempotence=%s ok=%s",
        thread_id,
        tool_op,
        idempotence,
        ok,
    )


@event_factory
def FrontierSdkAutoJobExpired(  # noqa: N802
    thread_id: str,
    deadline: str,
    elapsed_s: float,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.job_expired",
        payload={
            "thread_id": thread_id,
            "deadline": deadline,
            "elapsed_s": elapsed_s,
        },
        scope="node",
    )


def emit_job_expired(
    *,
    thread_id: str,
    deadline: str,
    elapsed_s: float,
) -> None:
    """Emit when a job passes its DIRECTIVE ``deadline:`` before execution."""
    emit_frontier_event(
        FrontierSdkAutoJobExpired(
            thread_id=thread_id,
            deadline=deadline,
            elapsed_s=elapsed_s,
        )
    )
    logger.info(
        "cursor-auto job_expired: thread_id=%s deadline=%s elapsed_s=%.1f",
        thread_id,
        deadline,
        elapsed_s,
    )


__all__ = [
    "emit_execute_admission_blocked",
    "emit_execute_op_ran",
    "emit_job_expired",
]
