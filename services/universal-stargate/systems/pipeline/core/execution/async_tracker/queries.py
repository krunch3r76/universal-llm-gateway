"""Record lookup and terminal-state waiting.

``get_record`` returns a record by id (pruning expired terminal records first),
and ``wait_for_terminal`` blocks up to a timeout for a record to reach a
terminal state via its ``asyncio.Event``. The tracker's public ``get`` /
``wait_for_terminal`` methods delegate here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .prune import _prune_terminal_records

if TYPE_CHECKING:
    from .records import PipelineExecutionRecord
    from .tracker import PipelineExecutionTracker


def get_record(
    tracker: PipelineExecutionTracker, execution_id: str
) -> PipelineExecutionRecord | None:
    """Return the record for ``execution_id`` or ``None`` if unknown/expired."""
    _prune_terminal_records(tracker)
    return tracker.records.get(execution_id)


async def wait_for_terminal(
    tracker: PipelineExecutionTracker,
    execution_id: str,
    timeout_seconds: float,
) -> PipelineExecutionRecord | None:
    """Wait up to ``timeout_seconds`` for the record to reach a terminal state.

    Returns the record whether or not the wait elapsed; callers inspect
    ``record.status`` to decide how to respond. Returns ``None`` when the
    execution_id is unknown.
    """
    record = tracker.records.get(execution_id)
    if record is None:
        return None
    if record.status in {"completed", "failed"}:
        return record
    if timeout_seconds <= 0:
        return record
    try:
        await asyncio.wait_for(
            record.terminal_event.wait(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        pass
    return record
