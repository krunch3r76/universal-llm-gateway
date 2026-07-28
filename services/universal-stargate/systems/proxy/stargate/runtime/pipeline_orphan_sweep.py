"""Fail in-flight async pipeline tracker records before event-bus teardown.

On graceful Stargate restart, running ``pipeline_dispatch_tracker`` entries must
terminalize to Event Service so the dispatch board does not retain idle ghosts.
Unclean SIGKILL leaves no in-process tracker — board watermark logic (Slice C)
clears those survivors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


async def cancel_running_pipelines_for_shutdown(
    proxy: StargateProxy,
    *,
    reason: str = "process_shutdown",
) -> int:
    """Cancel or fail all running tracker records and publish bus terminals.

    Returns the number of executions reaped. Safe when the tracker is absent
    (router-only mode) or when no records are running.
    """
    tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
    if tracker is None:
        return 0

    task_index = _pipeline_task_index(proxy)
    event_bus = getattr(proxy, "event_bus", None)
    reaped = 0

    for execution_id, record in list(tracker.records.items()):
        if record.status != "running":
            continue
        task = task_index.get(execution_id)
        if task is not None and not task.done():
            task.cancel()
        tracker.fail_execution(
            execution_id,
            code="restart_orphan",
            message=f"Pipeline orphaned during {reason}.",
        )
        if event_bus is not None:
            from systems.pipeline.core.events.dispatch import PipelineDispatchCancelled

            # Await so terminals reach Event Service before bus teardown.
            await event_bus.publish_nowait(
                PipelineDispatchCancelled(
                    pipeline_id=record.pipeline,
                    execution_id=execution_id,
                    source=reason,
                )
            )
        reaped += 1
        logger.info(
            "Orphan-swept running pipeline execution_id=%s pipeline=%s reason=%s",
            execution_id,
            record.pipeline,
            reason,
        )
    return reaped


def _pipeline_task_index(proxy: StargateProxy) -> dict[str, Any]:
    app = getattr(proxy, "_fastapi_app", None)
    if app is None:
        return {}
    return getattr(app.state, "pipeline_task_index", {}) or {}
