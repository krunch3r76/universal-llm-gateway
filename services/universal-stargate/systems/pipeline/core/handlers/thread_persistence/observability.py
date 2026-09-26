"""Compaction observability helpers for thread-persistence handlers.

``publish_compaction_event`` builds an event from a factory and publishes it
fire-and-forget on the proxy event bus reachable from the ``PipelineContext``;
it silently no-ops when no bus exists and only logs if event construction fails.
Background tasks are held in a module set so they are not garbage-collected
early. Called by archive_user_turn, archive_assistant_turn, assemble_thread and
summarize_thread_v1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from universal_event_bus import Event
from universal_logging import get_logger

if TYPE_CHECKING:
    from ..protocol import PipelineContext

logger = get_logger(__name__)

# Retain fire-and-forget publish tasks until completion — unreferenced tasks
# may be GC'd before the event loop runs them.
_background_tasks: set[asyncio.Task[None]] = set()


def publish_compaction_event(
    context: PipelineContext,
    factory: Callable[..., Event],
    **payload: object,
) -> None:
    """Fire-and-forget publish of a compaction event onto the context event bus."""
    proxy = getattr(context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    if not event_bus:
        return
    try:
        event = factory(**payload)
    except Exception as exc:
        # Breadcrumb-only: cannot emit an event to report event-build failure.
        logger.warning("compaction event publish failed: %s", exc)
        return

    async def _publish() -> None:
        await event_bus.publish_nowait(event)

    task = asyncio.get_running_loop().create_task(_publish())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
