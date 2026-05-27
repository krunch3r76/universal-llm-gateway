"""Event-bus context resolution and publishing for step observability.

Two responsibilities:

- ``get_event_context`` — extract ``(pipeline_id, execution_id)`` from the
  executor's runtime context, returning ``"unknown"`` sentinels with an
  error log when either is absent. Callers rely on this to never raise.

- ``publish_event`` — fire-and-forget publish onto the context's event bus
  (via the proxy attached to the executor's context). If the event bus is
  unavailable the function logs a single WARN per ``StepObservability``
  instance (gated by ``obs._event_bus_warned``) and silently drops the
  event so step execution is never blocked by observability infrastructure.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_event_bus import Event
from universal_logging import get_logger

if TYPE_CHECKING:
    from .step_observability import StepObservability

logger = get_logger(__name__)


def get_event_context(obs: StepObservability) -> tuple[str, str]:
    """Extract pipeline_id and execution_id from context."""
    pipeline = getattr(obs._executor.context, "pipeline", None)
    if pipeline is None:
        logger.error("Missing context.pipeline - using 'unknown' for events")
        pipeline_id = "unknown"
    else:
        pipeline_id = pipeline.id

    execution_id = getattr(obs._executor.context, "execution_id", None)
    if execution_id is None:
        logger.error("Missing context.execution_id - using 'unknown' for events")
        execution_id = "unknown"

    return pipeline_id, execution_id


def publish_event(obs: StepObservability, event: Event) -> None:
    """
    Publish event via context's event bus (fire-and-forget).

    Logs WARN once if event_bus unavailable.
    """
    proxy = getattr(obs._executor.context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    if event_bus:
        asyncio.create_task(event_bus.publish_nowait(event))
    elif not obs._event_bus_warned:
        logger.warning("Event bus unavailable - events will not be published")
        obs._event_bus_warned = True
