"""Review-child spawn fold — nest under parent, clear restart orphans."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import classify_sdk_live
from scripts.model_manager.ui.dispatch_monitor.core.tests.test_sdk_posture import _row


def _live_sdk(frame):
    return [r for r in frame.sdk if r.terminal_ms is None]


def test_review_child_spawned_registers_handler() -> None:
    handled = set(Model().handled_signals)
    assert signals.SDK_REVIEW_CHILD_SPAWNED in handled


def test_spawn_attaches_child_under_parent_not_id_split() -> None:
    model = Model()
    parent_id = "51b424bd"
    child_id = "94fbd19a"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {
                "dispatch_id": parent_id,
                "execution_id": parent_id,
                "root_id": "6164",
                "model": "composer-2.5",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_COMPLETED,
            50_000,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_REVIEW_CHILD_SPAWNED,
            51_000,
            {
                "execution_id": child_id,
                "parent_execution_id": parent_id,
                "parent_thread_id": "6164",
                "reviewer_model": "gpt-5.5",
                "reviewer_identity": "gpt-5.5",
                "reviewer_rung": None,
                "executor_identity": "composer-2.5",
                "executor_rung": None,
                "dedupe_key": "dedupe-1",
            },
        )
    )
    frame = model.derive(52_000)
    child = next(r for r in frame.sdk if r.dispatch_id == child_id)
    assert child.review_child is True
    assert child.parent_execution_id == parent_id
    assert child.role == "reviewer"
    assert child.model == "gpt-5.5"
    assert child.terminal_ms is None

    live = _live_sdk(frame)
    assert len(live) == 1
    assert classify_sdk_live(live) == "solo"


def test_parent_terminal_closes_live_review_child() -> None:
    model = Model()
    parent_id = "parent-exec"
    child_id = "child-exec"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_REVIEW_CHILD_SPAWNED,
            2_000,
            {
                "execution_id": child_id,
                "parent_execution_id": parent_id,
                "parent_thread_id": "t1",
                "reviewer_model": "gpt-5.5",
                "reviewer_identity": "gpt-5.5",
                "reviewer_rung": None,
                "executor_identity": "composer-2.5",
                "executor_rung": None,
                "dedupe_key": "k",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_COMPLETED,
            3_000,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    frame = model.derive(4_000)
    child = next(r for r in frame.sdk if r.dispatch_id == child_id)
    assert child.terminal_ms is not None
    assert child.state == "orphaned"
    assert child.failure_reason == "parent_terminal"


def test_system_started_terminalizes_pre_watermark_review_child() -> None:
    model = Model()
    child_id = "94fbd19a"
    model.apply(
        Event(
            signals.SDK_REVIEW_CHILD_SPAWNED,
            1_000,
            {
                "execution_id": child_id,
                "parent_execution_id": "gone-parent",
                "parent_thread_id": "6164",
                "reviewer_model": "gpt-5.5",
                "reviewer_identity": "gpt-5.5",
                "reviewer_rung": None,
                "executor_identity": "composer-2.5",
                "executor_rung": None,
                "dedupe_key": "k",
            },
        )
    )
    model.apply(Event(signals.SYSTEM_STARTED, 60_000, {}))
    frame = model.derive(61_000)
    child = next(r for r in frame.sdk if r.dispatch_id == child_id)
    assert child.terminal_ms == 60_000
    assert child.failure_reason == "restart_orphan"


def test_live_parent_and_review_child_classifies_nested() -> None:
    live = [
        _row(
            "parent",
            root_id="6164",
            state="running",
            tool_call_count=5,
        ),
        _row(
            "child",
            root_id="6164",
            review_child=True,
            parent_execution_id="parent",
            model="gpt-5.5",
        ),
    ]
    assert classify_sdk_live(live) == "nested"
