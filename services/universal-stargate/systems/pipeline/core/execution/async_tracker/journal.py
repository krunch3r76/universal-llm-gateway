"""Out-of-process journaling hook for terminal records.

``_schedule_journal`` fire-and-forgets the optional journal writer so a terminal
transition is persisted without blocking the tracker. The writer itself is
wired/replaced via ``PipelineExecutionTracker.set_journal_writer``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .records import PipelineExecutionRecord
    from .tracker import PipelineExecutionTracker

logger = get_logger(__name__)


def _schedule_journal(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """Schedule sqlite journaling for a freshly-terminal record."""
    if tracker._journal_writer is None:
        return
    try:
        task = asyncio.create_task(tracker._journal_writer(record))
        tracker._pending_tasks.add(task)
        task.add_done_callback(tracker._pending_tasks.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to schedule dispatch journaling: %s", exc)


def _schedule_journal_transition(
    tracker: PipelineExecutionTracker, record: PipelineExecutionRecord
) -> None:
    """Schedule a ``started`` journal fold on admission."""
    writer = getattr(tracker, "_transition_writer", None)
    if writer is None:
        return
    try:
        task = asyncio.create_task(writer(record))
        tracker._pending_tasks.add(task)
        task.add_done_callback(tracker._pending_tasks.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to schedule started journal transition: %s", exc)
