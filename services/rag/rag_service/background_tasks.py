"""Registry of fire-and-forget asyncio tasks owned by the RAG service lifecycle.

``track_background_task`` adds a task to ``state._background_tasks`` so that
``lifecycle._shutdown`` can cancel and await every still-running task on exit.
Callers include ``lifecycle`` (the init task), ``dependency_activation`` (startup
scope-freshness repair), ``extraction_runtime`` (worker and model watcher) and
``watcher_runtime`` (reconcile and purge sweeps). Done tasks drop out of the set
automatically, so the set only holds live work.
"""

from __future__ import annotations

import asyncio

from . import state


def track_background_task(task: asyncio.Task[None]) -> None:
    """Register a lifecycle-owned task so shutdown can cancel and await it.

    Adds ``task`` to ``state._background_tasks`` and attaches a done callback that
    discards it again, so completed tasks do not accumulate. Does not start,
    cancel or await the task itself; ``lifecycle._shutdown`` does the cancelling.
    """
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
