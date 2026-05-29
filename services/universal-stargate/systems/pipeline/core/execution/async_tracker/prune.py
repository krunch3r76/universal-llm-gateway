"""TTL pruning of terminal records.

``_prune_terminal_records`` drops terminal records past ``retention_seconds``
and emits ``pipeline.dispatch.tracker.expired`` per pruned record. Running
records are never age-evicted (see the package ``__init__`` module docstring for
the full pruning invariant).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ...events.dispatch import PipelineDispatchTrackerExpired
from .tracker_events import _emit

if TYPE_CHECKING:
    from .records import PipelineExecutionRecord
    from .tracker import PipelineExecutionTracker


def _prune_terminal_records(tracker: PipelineExecutionTracker) -> None:
    """Drop terminal records whose age exceeds ``retention_seconds``.

    Emits ``pipeline.dispatch.tracker.expired`` per pruned record so the
    rate of un-collected results is observable — informs whether the
    retention window is sufficient in practice.
    """
    now_monotonic = time.monotonic()
    expired: list[tuple[str, PipelineExecutionRecord, float]] = []
    for exec_id, record in tracker.records.items():
        if record.completed_at_monotonic is None:
            continue
        age = now_monotonic - record.completed_at_monotonic
        if age > tracker.retention_seconds:
            expired.append((exec_id, record, age))
    for exec_id, record, age in expired:
        tracker.records.pop(exec_id, None)
        _emit(
            tracker,
            PipelineDispatchTrackerExpired(
                pipeline_id=record.pipeline,
                execution_id=exec_id,
                status=record.status,
                age_seconds=age,
            ),
        )
