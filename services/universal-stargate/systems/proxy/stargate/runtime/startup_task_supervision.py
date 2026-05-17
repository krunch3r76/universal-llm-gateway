from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def schedule_supervised_task(coro: Coroutine[Any, Any, object], name: str) -> None:
    """
    Schedule a background task and surface exceptions in logs.

    Used when wiring callbacks asynchronously so startup and event handlers remain
    non-blocking while task failures remain diagnosable.
    """
    task = asyncio.create_task(coro, name=name)

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        """Handle completion callback for a supervised background task.

        Args:
            done_task: Completed asyncio task instance.
        """
        if done_task.cancelled():
            logger.debug("Background task cancelled: %s", name)
            return
        exc = done_task.exception()
        if exc is not None:
            logger.error(
                "Background task failed: %s: %s",
                name,
                exc,
                exc_info=True,
            )

    task.add_done_callback(_on_done)
