"""B-opp regression tests — caller identity + dispatch ops."""

from __future__ import annotations

import pytest

from systems.pipeline.core.execution.async_tracker import PipelineExecutionTracker


@pytest.mark.asyncio
async def test_register_records_caller_agent() -> None:
    tracker = PipelineExecutionTracker()
    record = tracker.register_execution(
        execution_id="e1",
        pipeline="frontier-dispatch",
        started_at="2026-04-19T00:00:00Z",
        caller_agent="orion",
    )
    assert record.caller_agent == "orion"
    tracked = tracker.get("e1")
    assert tracked is not None
    assert tracked.caller_agent == "orion"


def test_tracker_stats_shape() -> None:
    tracker = PipelineExecutionTracker()
    tracker.register_execution(execution_id="e1", pipeline="p", started_at="t0")
    tracker.register_execution(execution_id="e2", pipeline="p", started_at="t0")
    tracker.complete_execution(
        "e2", content="ok", model="m", usage=None, duration_s=1.0
    )

    running = sum(
        1 for record in tracker.records.values() if record.status == "running"
    )
    completed = sum(
        1 for record in tracker.records.values() if record.status == "completed"
    )
    assert running == 1
    assert completed == 1
