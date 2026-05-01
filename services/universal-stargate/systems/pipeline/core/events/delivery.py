"""Async-dispatch result-delivery lifecycle events.

Node-scoped signals emitted by ``async_tracker_delivery`` when Stargate
posts a dispatched pipeline's terminal state to an agent-bus thread.
Complements ``pipeline.dispatch.*`` (admission / terminal state) with
the *delivery-side* lifecycle.

Invariants:
- ∀ delivery attempt: emit exactly one of ``.sent``, ``.failed``, ``.skipped``.
- ``.skipped`` means ``result_delivery`` was absent or incomplete — not
  an error.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineDispatchDeliverySent(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    thread: str,
    to_agent: str,
    from_agent: str,
) -> Event:
    """Emitted when a terminal-state turn has been posted successfully."""
    return Event(
        signal="pipeline.dispatch.delivery.sent",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "thread": thread,
            "to_agent": to_agent,
            "from_agent": from_agent,
        },
    )


@event_factory
def PipelineDispatchDeliveryFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    """Emitted when the agent-bus POST returned non-2xx or failed transport.

    Tracker record is unchanged — poll endpoint still returns the
    terminal result. Callers that care about reliable delivery should
    inspect this event stream or re-drive via MCP.
    """
    return Event(
        signal="pipeline.dispatch.delivery.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "thread": thread,
            "status_code": status_code,
            "error_preview": error_preview,
        },
    )


@event_factory
def PipelineDispatchDeliverySkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    reason: str,
) -> Event:
    """Emitted when delivery was not attempted.

    Reasons:
        ``no_delivery_config`` — ``result_delivery`` absent on the record
        ``incomplete_delivery_config`` — required field (thread / from /
            to) missing
    """
    return Event(
        signal="pipeline.dispatch.delivery.skipped",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "reason": reason,
        },
    )


@event_factory
def PipelineDispatchDeliveryCloseFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    """Emitted when ephemeral thread close failed after a successful delivery.

    Delivery itself succeeded (``pipeline.dispatch.delivery.sent`` was already
    emitted).  This event signals only cleanup failure — the tracker record
    and the delivery POST are both unaffected.  Alerts on this signal surface
    orphaned threads without false-positiving on delivery failures.
    """
    return Event(
        signal="pipeline.dispatch.delivery.close.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "thread": thread,
            "status_code": status_code,
            "error_preview": error_preview,
        },
    )


@event_factory
def AgentBusDispatchAdmitFailed(  # noqa: N802
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    """Emitted when the POST /threads/{id}/dispatch-admit call fails.

    Signal: mcp.agentbus.dispatch.admit.failed — shared vocabulary with
    agent-bus lifecycle events. Tracker admission and record state are
    unchanged; the failure is observable but non-fatal.
    """
    return Event(
        signal="mcp.agentbus.dispatch.admit.failed",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "status_code": status_code,
            "error_preview": error_preview,
        },
    )


@event_factory
def AgentBusThreadClosedEphemeral(  # noqa: N802
    thread: str,
) -> Event:
    """Emitted from Stargate when ephemeral delivery cleanup closes a thread.

    Uses the shared ``mcp.agentbus.thread.closed`` signal so event queries
    pick it up alongside manual closes.  ``via=ephemeral_delivery``
    distinguishes automated cleanup from ``via=reply`` (reply-with-close)
    and ``via=manual`` (explicit close calls).
    """
    return Event(
        signal="mcp.agentbus.thread.closed",
        payload={"thread": thread, "via": "ephemeral_delivery"},
    )
