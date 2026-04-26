"""Track asyncio tasks for coordinated shutdown (see _shutdown in lifecycle)."""

from __future__ import annotations

import asyncio

from . import state


def track_background_task(task: asyncio.Task[None]) -> None:
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
