"""Async pipeline dispatch tracker event factories.

Covers admission, terminal-state, rejection, cancellation, and TTL-pruning
signals. Node-scoped only by signal namespace — these factories do NOT set
``scope="node"`` (they emit on the global bus alongside the older
``pipeline.started`` / ``pipeline.completed`` / ``pipeline.failed`` signals).
Journal events live in the sibling ``journal`` submodule and DO carry
``scope="node"``.

Consumers:
- ``core/execution/async_tracker.py`` — emits Async, Completed, Rejected,
  TrackerExpired
- ``proxy/routers/api/pipelines_dispatch.py`` — emits Cancelled (lazy import
  inside the handler)

Signals: ``pipeline.dispatch.{async,completed,cancelled,rejected,tracker.expired}``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineDispatchAsync(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    has_delivery_hook: bool,
    caller_agent: str | None = None,
    op: str = "",
    output_contract: str = "inline",
    endpoint_request_id: str | None = None,
) -> Event:
    """Emitted when the async tracker admits a new execution.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Newly minted execution UUID
        has_delivery_hook: Whether a delivery config was supplied
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        output_contract: Where the work product lands (``inline`` | ``thread``)
        endpoint_request_id: Endpoint ``request_id`` when admitted via a
            canonical dispatch route (join key for ``dispatch.skills.*``)
    """
    payload: dict[str, object] = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "has_delivery_hook": has_delivery_hook,
        "caller_agent": caller_agent,
        "op": op,
        "output_contract": output_contract,
    }
    if endpoint_request_id is not None:
        payload["endpoint_request_id"] = endpoint_request_id
    return Event(
        signal="pipeline.dispatch.async",
        payload=payload,
    )


@event_factory
def PipelineDispatchCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    status: str,
    duration_s: float,
    caller_agent: str | None = None,
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    """Emitted when the async tracker records a terminal state.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Execution UUID
        status: Terminal status (``completed`` or ``failed``)
        duration_s: Wall-clock seconds from admission to terminal transition
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        output_contract: Where the work product lands (``inline`` | ``thread``)
    """
    return Event(
        signal="pipeline.dispatch.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "status": status,
            "duration_s": duration_s,
            "caller_agent": caller_agent,
            "op": op,
            "output_contract": output_contract,
        },
    )


@event_factory
def PipelineDispatchCancelled(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    source: str,
) -> Event:
    """Emitted when a running dispatch is cancelled by an explicit DELETE."""
    return Event(
        signal="pipeline.dispatch.cancelled",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "source": source,
        },
    )


@event_factory
def PipelineDispatchRejected(  # noqa: N802
    pipeline_id: str,
    reason: str,
) -> Event:
    """Emitted when the async tracker refuses to admit a new execution.

    Payload:
        pipeline_id: Requested pipeline identifier
        reason: Rejection reason (e.g. ``capacity_exhausted``)
    """
    return Event(
        signal="pipeline.dispatch.rejected",
        payload={
            "pipeline_id": pipeline_id,
            "reason": reason,
        },
    )


@event_factory
def PipelineDispatchTrackerExpired(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    status: str,
    age_seconds: float,
) -> Event:
    """Emitted when a terminal tracker record is pruned by TTL.

    Gives observability on whether the retention window is long enough in
    practice: if a caller never polled before the record expired, the result
    is gone. Tracking the frequency of these events informs whether to bump
    retention further or move to persistent storage (phase 2+).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Execution UUID being dropped
        status: Terminal status the record held (``completed`` or ``failed``)
        age_seconds: Seconds elapsed since terminal transition
    """
    return Event(
        signal="pipeline.dispatch.tracker.expired",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "status": status,
            "age_seconds": age_seconds,
        },
    )
