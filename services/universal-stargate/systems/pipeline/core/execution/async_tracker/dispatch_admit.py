"""Agent-bus dispatch-admit side effect.

After ``register_execution`` admits a record bound for a bus thread,
``_schedule_dispatch_admit`` fire-and-forgets a POST to
``/threads/{id}/dispatch-admit``. No-op when no agent-bus token is configured;
failures emit ``mcp.agentbus.dispatch.admit.failed`` and are otherwise swallowed
so the admission path is never affected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from transport_utils import make_async_client
from universal_logging import get_logger

from .tracker_events import _emit

if TYPE_CHECKING:
    from .records import PipelineExecutionRecord
    from .tracker import PipelineExecutionTracker

logger = get_logger(__name__)


def _schedule_dispatch_admit(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """Fire-and-forget POST /threads/{id}/dispatch-admit after register_execution.

    No-op when agent_bus_token is unset (disabled path). Failures emit
    mcp.agentbus.dispatch.admit.failed and are otherwise swallowed so
    the tracker admission path is never affected.
    """
    if not tracker._agent_bus_token:
        return
    try:
        task = asyncio.create_task(_do_dispatch_admit(tracker, record))
        tracker._pending_tasks.add(task)
        task.add_done_callback(tracker._pending_tasks.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to schedule dispatch-admit for %s: %s", record.execution_id, exc
        )


async def _do_dispatch_admit(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """POST dispatch-admit to agent-bus; emit failure event on error."""
    from ...events.delivery import AgentBusDispatchAdmitFailed

    delivery = record.result_delivery or {}
    thread = delivery.get("bus_thread", "")
    pipeline_id = record.pipeline

    payload = {
        "execution_id": record.execution_id,
        "pipeline_id": pipeline_id,
        "caller_agent": record.caller_agent,
    }
    try:
        async with make_async_client(tracker._agent_bus_url, timeout=10.0) as client:
            response = await client.post(
                f"/threads/{thread}/dispatch-admit",
                headers={"Authorization": f"Bearer {tracker._agent_bus_token}"},
                json=payload,
            )
            if response.status_code not in (200, 201):
                _emit(
                    tracker,
                    AgentBusDispatchAdmitFailed(
                        execution_id=record.execution_id,
                        thread=thread,
                        status_code=response.status_code,
                        error_preview=response.text[:200],
                    ),
                )
    except Exception as exc:
        _emit(
            tracker,
            AgentBusDispatchAdmitFailed(
                execution_id=record.execution_id,
                thread=thread,
                status_code=0,
                error_preview=str(exc)[:200],
            ),
        )
