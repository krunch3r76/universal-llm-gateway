"""Fire-and-forget dispatch-event emission.

``_emit`` centralizes the sync-caller / async-publish bridge: tracker call
sites are sync but the event bus' ``publish_nowait`` is a coroutine, so the
publish is scheduled via ``asyncio.create_task`` and tracked in
``tracker._pending_tasks``. Silent no-op when no bus is wired.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import Event

    from .tracker import PipelineExecutionTracker

logger = get_logger(__name__)


def _emit(tracker: PipelineExecutionTracker, event: Event) -> None:
    """Fire-and-forget publish; drop silently if no bus is wired.

    ``publish_nowait`` is an async method on the real event bus (the
    name refers to not blocking on subscribers, not to being sync).
    Wrapping in ``asyncio.create_task`` lets ``_emit`` remain sync while
    the coroutine actually gets scheduled. All tracker call sites run in
    an async context, so a running loop is guaranteed.
    """
    if tracker.event_bus is None:
        return
    try:
        task = asyncio.create_task(tracker.event_bus.publish_nowait(event))
        tracker._pending_tasks.add(task)
        task.add_done_callback(tracker._pending_tasks.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to publish dispatch event: %s", exc)
