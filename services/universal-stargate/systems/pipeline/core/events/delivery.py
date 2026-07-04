"""Async-dispatch result-delivery lifecycle events.

Node-scoped signals emitted by ``async_tracker_delivery`` when Stargate
posts a dispatched pipeline's terminal state to an agent-bus thread.
Complements ``pipeline.dispatch.*`` (admission / terminal state) with
the *delivery-side* lifecycle.

Invariants:
- ∀ delivery attempt: emit exactly one of ``.sent``, ``.failed``, ``.skipped``.
- ``.skipped`` means ``result_delivery`` was absent or incomplete — not
  an error.
- Bus-mode (``op="to_thread"``): ``.sent`` on the on-behalf POST 2xx;
  ``.failed`` on POST non-2xx, oversized sidecar write failure, or
  unresolved to_agent.
  The legacy ``pipeline.dispatch.delivery.completed`` signal (reply-
  observation success) was retired with the 2026-05-22 delivery
  architectural fix.
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
    op: str = "",
    output_contract: str = "inline",
    delivery_mode: str = "inline",
    sidecar_uri: str | None = None,
    content_sha256: str | None = None,
    sidecar_status: str = "ok",
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
            "op": op,
            "output_contract": output_contract,
            "delivery_mode": delivery_mode,
            "sidecar_uri": sidecar_uri,
            "content_sha256": content_sha256,
            "sidecar_status": sidecar_status,
        },
    )


@event_factory
def PipelineDispatchDeliveryFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
    op: str = "",
    output_contract: str = "inline",
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
            "op": op,
            "output_contract": output_contract,
        },
    )


@event_factory
def PipelineDispatchDeliverySkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    reason: str,
    op: str = "",
    output_contract: str = "inline",
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
            "op": op,
            "output_contract": output_contract,
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
def DispatchThreadReused(  # noqa: N802
    thread: str,
    dispatch_thread_id: str | None,
    lane: str,
    is_auto: bool,
) -> Event:
    """Emitted when generate reuses an existing agent-bus thread."""
    return Event(
        signal="mcp.agentbus.dispatch.thread.reused",
        payload={
            "thread": thread,
            "dispatch_thread_id": dispatch_thread_id,
            "lane": lane,
            "is_auto": is_auto,
        },
    )


@event_factory
def DispatchThreadSplit(  # noqa: N802
    thread: str,
    dispatch_thread_id: str | None,
    lane: str,
) -> Event:
    """Emitted when generate mints a sibling thread instead of reusing."""
    return Event(
        signal="mcp.agentbus.dispatch.thread.split",
        payload={
            "thread": thread,
            "dispatch_thread_id": dispatch_thread_id,
            "lane": lane,
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
