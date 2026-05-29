"""Bus delivery scheduling and outcome-driven status demotion.

``_schedule_delivery`` fans a freshly-terminal record into the delivery sender
(emitting ``.skipped`` when no delivery config is present), and
``_run_delivery_with_outcome`` invokes the sender and may demote an
``op="to_thread"`` record from ``completed`` to ``failed`` when the on-behalf
POST fails (architectural fix 2026-05-22). Journaling for ``to_thread`` records
is deferred to here so the journal entry carries the final status.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ...events.dispatch import PipelineDispatchCompleted
from .journal import _schedule_journal
from .records import PipelineExecutionError
from .tracker_events import _emit

if TYPE_CHECKING:
    from .records import PipelineExecutionRecord
    from .tracker import PipelineExecutionTracker

logger = get_logger(__name__)


def _schedule_delivery(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """Schedule bus delivery for a freshly-terminal record.

    No-op when no sender is wired. When a sender is wired but the
    record has no delivery config (neither legacy ``result_delivery``
    nor ``op="to_thread"`` with a ``target_thread``), emit ``.skipped``
    once so observability can distinguish "no hook configured at startup"
    (silent) from "hook present but no delivery config on this record"
    (.skipped) from "hook failed" (.failed).

    Phase 2: wraps the delivery call in ``_run_delivery_with_outcome``
    so ``op="to_thread"`` timeout failures can demote a provisional
    ``completed`` record to ``failed`` after reply observation exhausts.
    """
    if tracker._delivery_sender is None:
        return
    # For bus-mode records, reply observation only makes sense when the model
    # itself completed. If the model failed, there is no agent reply to observe.
    if record.op == "to_thread" and record.status != "completed":
        return
    has_delivery_config = record.result_delivery is not None or (
        record.op == "to_thread" and record.target_thread is not None
    )
    if not has_delivery_config:
        from ...events.delivery import PipelineDispatchDeliverySkipped

        _emit(
            tracker,
            PipelineDispatchDeliverySkipped(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                reason="no_delivery_config",
            ),
        )
        return
    try:
        task = asyncio.create_task(_run_delivery_with_outcome(tracker, record))
        tracker._pending_tasks.add(task)
        task.add_done_callback(tracker._pending_tasks.discard)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to schedule dispatch delivery: %s", exc)


async def _run_delivery_with_outcome(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """Invoke delivery sender; demote record to failed on bus-mode delivery failure.

    For ``op="to_thread"`` dispatches the record reaches ``completed`` from
    the model-completion side, but the true success criterion is the
    system-on-behalf POST landing on the target thread (architectural
    fix 2026-05-22 — see
    ``notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md``).
    If the delivery sender returns
    ``DeliveryOutcome(status="failed", failure_reason=…)``, this method
    demotes the record from ``completed`` to ``failed`` and schedules
    journaling so the final journal entry reflects the actual outcome.

    For all other outcomes (delivered, skipped, non-bus-mode) the record
    is unchanged here — side-effects (events, mutations) are the delivery
    sender's responsibility.
    """
    from ..async_tracker_delivery import DeliveryOutcome

    try:
        result = await tracker._delivery_sender(record)  # type: ignore[misc]
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Delivery sender raised unexpectedly: %s", exc)
        result = None

    if not isinstance(result, DeliveryOutcome):
        # Legacy senders returned None; treat as delivered (no demotion).
        if record.op == "to_thread":
            _schedule_journal(tracker, record)
        return

    if (
        result.status == "failed"
        and record.op == "to_thread"
        and record.status == "completed"
    ):
        failure_reason = result.failure_reason or "delivery_failed"
        record.status = "failed"
        record.error = PipelineExecutionError(
            code=failure_reason,
            message=(
                f"On-behalf reply post to thread {record.target_thread!r} "
                f"failed: {failure_reason}."
            ),
        )
        _emit(
            tracker,
            PipelineDispatchCompleted(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                status="failed",
                duration_s=time.monotonic() - record.started_at_monotonic,
                caller_agent=record.caller_agent,
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )

    # Journal deferred from complete_execution for to_thread records.
    if record.op == "to_thread":
        _schedule_journal(tracker, record)
