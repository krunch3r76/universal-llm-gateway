"""Hermetic contract: shutdown orphan-sweep fails running tracker rows + publishes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from systems.pipeline.core.execution.async_tracker.records import (
    PipelineExecutionRecord,
)
from systems.proxy.stargate.runtime.pipeline_orphan_sweep import (
    cancel_running_pipelines_for_shutdown,
)


@pytest.mark.asyncio
async def test_sweep_fails_running_and_publishes_cancelled() -> None:
    record = PipelineExecutionRecord(
        execution_id="exec-1",
        pipeline="frontier-dispatch",
        status="running",
        started_at="2026-01-01T00:00:00Z",
        started_at_monotonic=0.0,
    )
    tracker = MagicMock()
    tracker.records = {"exec-1": record}
    tracker.fail_execution = MagicMock()

    published: list[object] = []

    async def capture(event: object) -> None:
        published.append(event)

    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock(side_effect=capture)

    proxy = MagicMock()
    proxy.pipeline_dispatch_tracker = tracker
    proxy.event_bus = event_bus
    proxy._fastapi_app = None

    count = await cancel_running_pipelines_for_shutdown(
        proxy, reason="process_shutdown"
    )

    assert count == 1
    tracker.fail_execution.assert_called_once()
    call_kw = tracker.fail_execution.call_args
    assert call_kw[0][0] == "exec-1"
    assert call_kw[1]["code"] == "restart_orphan"
    await asyncio.sleep(0)
    assert len(published) == 1
    assert published[0].signal == "pipeline.dispatch.cancelled"
    assert published[0].payload["execution_id"] == "exec-1"
    assert published[0].payload["source"] == "process_shutdown"
