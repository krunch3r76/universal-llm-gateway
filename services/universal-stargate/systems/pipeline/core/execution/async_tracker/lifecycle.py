"""Execution lifecycle transitions: admit, complete, fail.

``register_execution`` admits a new record (pruning + capacity eviction, then
emitting ``pipeline.dispatch.async``), while ``complete_execution`` /
``fail_execution`` drive the idempotent terminal transitions (emitting
``pipeline.dispatch.completed`` and fanning out delivery + journaling). The
tracker's public methods of the same names delegate here. See the package
``__init__`` module docstring for the full emission invariants.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from universal_logging import get_logger

from ...events.dispatch import (
    PipelineDispatchAsync,
    PipelineDispatchCompleted,
    PipelineDispatchRejected,
)
from .constants import _utc_now_iso
from .delivery_hooks import _schedule_delivery
from .dispatch_admit import _schedule_dispatch_admit
from .errors import TrackerCapacityError
from .journal import _schedule_journal
from .prune import _prune_terminal_records
from .records import (
    PipelineExecutionError,
    PipelineExecutionRecord,
    PipelineExecutionResult,
)
from .tracker_events import _emit

if TYPE_CHECKING:
    from .tracker import PipelineExecutionTracker

logger = get_logger(__name__)


def register_execution(
    tracker: PipelineExecutionTracker,
    *,
    execution_id: str,
    pipeline: str,
    started_at: str,
    result_delivery: dict[str, Any] | None = None,
    caller_agent: str | None = None,
    output_contract: Literal["inline", "thread"] = "inline",
    target_thread: str | None = None,
    op: Literal["generate", "to_thread"] | None = None,
    from_agent: str | None = None,
    reply_subject: str | None = None,
    bus_lifecycle: Literal["persistent", "ephemeral"] = "ephemeral",
    endpoint_request_id: str | None = None,
) -> PipelineExecutionRecord:
    """Admit a new execution and emit ``pipeline.dispatch.async``.

    Raises ``TrackerCapacityError`` (and emits ``pipeline.dispatch.rejected``)
    when saturation cannot be relieved by evicting a terminal record.
    """
    _prune_terminal_records(tracker)

    if len(tracker.records) >= tracker.max_records:
        terminal_id = next(
            (
                eid
                for eid, rec in tracker.records.items()
                if rec.status in {"completed", "failed"}
            ),
            None,
        )
        if terminal_id is not None:
            tracker.records.pop(terminal_id, None)
        else:
            _emit(
                tracker,
                PipelineDispatchRejected(
                    pipeline_id=pipeline,
                    reason="capacity_exhausted",
                ),
            )
            raise TrackerCapacityError(
                "Async pipeline tracker capacity exhausted; all slots running"
            )

    record = PipelineExecutionRecord(
        execution_id=execution_id,
        pipeline=pipeline,
        status="running",
        started_at=started_at,
        started_at_monotonic=time.monotonic(),
        result_delivery=result_delivery,
        caller_agent=caller_agent,
        output_contract=output_contract,
        target_thread=target_thread,
        op=op,
        from_agent=from_agent,
        reply_subject=reply_subject,
        bus_lifecycle=bus_lifecycle,
        endpoint_request_id=endpoint_request_id,
    )
    tracker.records[execution_id] = record

    has_delivery_hook = result_delivery is not None or (
        op == "to_thread" and target_thread is not None
    )
    _emit(
        tracker,
        PipelineDispatchAsync(
            pipeline_id=pipeline,
            execution_id=execution_id,
            has_delivery_hook=has_delivery_hook,
            caller_agent=record.caller_agent,
            op=op or "",
            output_contract=output_contract,
            endpoint_request_id=endpoint_request_id,
        ),
    )

    if result_delivery and result_delivery.get("bus_thread"):
        _schedule_dispatch_admit(tracker, record)

    return record


def complete_execution(
    tracker: PipelineExecutionTracker,
    execution_id: str,
    *,
    content: str,
    model: str,
    model_entity_id: str | None = None,
    usage: dict[str, Any] | None,
    duration_s: float,
    reasoning: Any = None,
    hints: list[dict[str, Any]] | None = None,
) -> None:
    """Record success terminal state (idempotent)."""
    record = tracker.records.get(execution_id)
    if record is None or record.status in {"completed", "failed"}:
        logger.warning(
            "Ignoring duplicate/unknown terminal update execution_id=%s",
            execution_id,
        )
        return
    record.status = "completed"
    record.completed_at = _utc_now_iso()
    record.completed_at_monotonic = time.monotonic()
    record.result = PipelineExecutionResult(
        content=content,
        model=model,
        model_entity_id=model_entity_id,
        usage=usage,
        duration_s=duration_s,
        reasoning=reasoning,
        hints=hints,
    )
    record.terminal_event.set()
    _emit(
        tracker,
        PipelineDispatchCompleted(
            pipeline_id=record.pipeline,
            execution_id=execution_id,
            status="completed",
            duration_s=duration_s,
            caller_agent=record.caller_agent,
            op=record.op or "",
            output_contract=record.output_contract,
        ),
    )
    _schedule_delivery(tracker, record)
    # Phase 2: for op="to_thread" records, journal is deferred until after
    # reply observation completes (via _run_delivery_with_outcome). This
    # ensures exactly one journal entry per record, carrying the FINAL status
    # (either "completed" on reply observed, or "failed" on timeout).
    if record.op != "to_thread":
        _schedule_journal(tracker, record)


def fail_execution(
    tracker: PipelineExecutionTracker,
    execution_id: str,
    *,
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Record failure terminal state (idempotent).

    ``data`` carries the structured upstream error body when the
    failure came from a provider HTTP 4xx/5xx with a JSON response.
    See ``PipelineExecutionError`` for the shape contract.
    """
    record = tracker.records.get(execution_id)
    if record is None or record.status in {"completed", "failed"}:
        logger.warning(
            "Ignoring duplicate/unknown terminal update execution_id=%s",
            execution_id,
        )
        return
    record.status = "failed"
    record.completed_at = _utc_now_iso()
    record.completed_at_monotonic = time.monotonic()
    duration = record.completed_at_monotonic - record.started_at_monotonic
    record.error = PipelineExecutionError(code=code, message=message, data=data)
    record.terminal_event.set()
    _emit(
        tracker,
        PipelineDispatchCompleted(
            pipeline_id=record.pipeline,
            execution_id=execution_id,
            status="failed",
            duration_s=duration,
            caller_agent=record.caller_agent,
            op=record.op or "",
            output_contract=record.output_contract,
        ),
    )
    _schedule_delivery(tracker, record)
    _schedule_journal(tracker, record)
