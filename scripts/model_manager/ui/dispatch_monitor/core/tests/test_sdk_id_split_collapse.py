"""SdkFold collapses execution_id / dispatch_id dual rows at ingest (id_split)."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import classify_sdk_live


def _live_sdk(frame):
    return [row for row in frame.sdk if row.terminal_ms is None]


def test_default_generate_path_yields_single_row() -> None:
    """6197-shaped sequence with dual-stamp dispatched → one live row + solo."""
    execution_id = "6aa001c8"
    dispatch_id = "24032c17ed4c-75c12574"
    thread_id = "6197"
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.dispatched",
            1_000,
            {
                "request_id": "24032c17ed4c",
                "execution_id": execution_id,
                "dispatch_id": dispatch_id,
                "thread_id": thread_id,
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_100,
            {
                "dispatch_id": dispatch_id,
                "thread_id": thread_id,
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_200,
            {
                "dispatch_id": dispatch_id,
                "thread_id": thread_id,
                "call_id": "tc-1",
            },
        )
    )
    assert len(model.sdk.dispatches) == 1
    assert dispatch_id in model.sdk.dispatches
    frame = model.derive(1_300)
    live = _live_sdk(frame)
    assert len(live) == 1
    assert live[0].dispatch_id == dispatch_id
    assert classify_sdk_live(live) == "solo"


def test_stargate_execution_id_then_worker_dispatch_id_one_live_row() -> None:
    """Stargate early events on E, worker lane on D≠E — one live row keyed on D."""
    execution_id = "39369a01-stargate-exec"
    dispatch_id = "5ed13fdd78a1-badcb93b"
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.queued",
            1_000,
            {
                "request_id": "5ed13fdd78a1",
                "execution_id": execution_id,
                "queue_position": 2,
                "origin_service": "stargate",
            },
        )
    )
    model.apply(
        Event(
            "monitor.meta.sdk_started",
            1_100,
            {"execution_id": execution_id, "thread_id": "6164"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_200,
            {
                "dispatch_id": dispatch_id,
                "execution_id": execution_id,
                "thread_id": "6164",
                "resolved_model": "cursor/grok-4.6",
                "tool_call_count": 5,
            },
        )
    )
    frame = model.derive(1_300)
    live = _live_sdk(frame)
    assert len(live) == 1
    assert live[0].dispatch_id == dispatch_id
    assert live[0].tool_call_count == 5

    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_400,
            {
                "execution_id": execution_id,
                "dispatch_id": dispatch_id,
                "call_id": "tc-1",
                "tool_name": "mcp",
                "status": "completed",
            },
        )
    )
    row = next(r for r in model.derive(1_500).sdk if r.dispatch_id == dispatch_id)
    assert row.last_tool_name == "mcp"
    assert row.terminal_ms is None

    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            2_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": execution_id,
                "status": "completed",
            },
        )
    )
    frame_done = model.derive(2_100)
    assert _live_sdk(frame_done) == []
    done = next(r for r in frame_done.sdk if r.dispatch_id == dispatch_id)
    assert done.terminal_ms == 2_000
    assert done.state == "completed"
    assert not any(r.dispatch_id == execution_id for r in frame_done.sdk)


def test_id_split_collapse_preserves_nested_park() -> None:
    """Parent/child park rows with distinct ids must not be collapsed together."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_000,
            {
                "dispatch_id": "exec-park-parent",
                "execution_id": "exec-park-parent",
                "thread_id": "6105",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.lease.park_enter",
            1_100,
            {
                "parent_id": "exec-park-parent",
                "child_id": "exec-park-child",
                "source_repo": "universal-llm-gateway",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_200,
            {"execution_id": "exec-park-child", "thread_id": "6106"},
        )
    )
    frame = model.derive(1_300)
    live = _live_sdk(frame)
    assert len(live) == 2
    states = {row.dispatch_id: row.state for row in live}
    assert states["exec-park-parent"] == "parked_waiting"
    assert states["exec-park-child"] == "running"
