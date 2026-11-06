"""Round-5/6 — shared tracker lock prevents duplicate register_execution."""

from __future__ import annotations

import threading
import time
from typing import Any

from execution_conformance.adapters import StargatePipelineAdapter
from systems.pipeline.core.execution.async_tracker.tracker import (
    PipelineExecutionTracker,
)


def test_p4_stargate_two_adapters_share_lock() -> None:
    tracker = PipelineExecutionTracker(event_bus=None)
    a = StargatePipelineAdapter(tracker)
    b = StargatePipelineAdapter(tracker)
    assert a.lock is b.lock


def test_p4_stargate_race_fresh_execution_id_register_once() -> None:
    tracker = PipelineExecutionTracker(event_bus=None)
    exec_id = "stargate-fresh-race-exec"
    calls: list[str] = []
    barrier = threading.Barrier(2)
    real_register = tracker.register_execution

    def counting_register(**kwargs: Any):
        calls.append(kwargs.get("execution_id", ""))
        time.sleep(0.05)
        return real_register(**kwargs)

    tracker.register_execution = counting_register  # type: ignore[method-assign]

    def _run(adapter: StargatePipelineAdapter) -> None:
        barrier.wait(timeout=5.0)
        adapter.admit(exec_id, pipeline="race")

    a = StargatePipelineAdapter(tracker)
    b = StargatePipelineAdapter(tracker)
    t1 = threading.Thread(target=_run, args=(a,))
    t2 = threading.Thread(target=_run, args=(b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert calls.count(exec_id) == 1


def test_p4_stargate_completed_not_overwritten_to_running() -> None:
    tracker = PipelineExecutionTracker(event_bus=None)
    exec_id = "stargate-complete-race"
    barrier = threading.Barrier(2)
    real_register = tracker.register_execution

    def register_then_complete(**kwargs: Any):
        rec = real_register(**kwargs)
        if kwargs.get("execution_id") == exec_id:
            rec.status = "completed"
        return rec

    tracker.register_execution = register_then_complete  # type: ignore[method-assign]

    def _run(adapter: StargatePipelineAdapter) -> None:
        barrier.wait(timeout=5.0)
        adapter.admit(exec_id, pipeline="race")

    a = StargatePipelineAdapter(tracker)
    b = StargatePipelineAdapter(tracker)
    t1 = threading.Thread(target=_run, args=(a,))
    t2 = threading.Thread(target=_run, args=(b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    final = tracker.get(exec_id)
    assert final is not None
    assert final.status == "completed"
