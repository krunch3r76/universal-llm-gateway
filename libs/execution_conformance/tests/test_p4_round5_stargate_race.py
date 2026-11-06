"""Round-5 — shared tracker lock prevents register_execution races (Minor 6)."""

from __future__ import annotations

import threading

from execution_conformance.adapters import StargatePipelineAdapter
from systems.pipeline.core.execution.async_tracker.tracker import (
    PipelineExecutionTracker,
)


def test_p4_stargate_two_adapters_one_tracker_no_status_regression() -> None:
    tracker = PipelineExecutionTracker(event_bus=None)
    exec_id = "stargate-race-exec-id"
    tracker.register_execution(
        execution_id=exec_id,
        pipeline="race",
        started_at="2026-01-01T00:00:00Z",
    )
    record = tracker.get(exec_id)
    assert record is not None
    record.status = "completed"

    errors: list[BaseException] = []

    def _lookup_admit(adapter: StargatePipelineAdapter) -> None:
        try:
            adapter.lookup(exec_id)
            adapter.admit(exec_id, pipeline="race")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    a = StargatePipelineAdapter(tracker)
    b = StargatePipelineAdapter(tracker)
    t1 = threading.Thread(target=_lookup_admit, args=(a,))
    t2 = threading.Thread(target=_lookup_admit, args=(b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    final = tracker.get(exec_id)
    assert final is not None
    assert final.status == "completed"
