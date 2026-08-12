"""Cancel/supersede observation events for cursor-sdk dispatches."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_auto import supersede as auto_supersede
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_supersede import (
    escalate_supersede_abort,
    register_live_run,
    signal_supersede,
    unregister_live_run,
)


@pytest.fixture
def emitted(monkeypatch):
    events: list = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_cancel_events.emit_frontier_event",
        lambda event: events.append(event),
    )
    return events


def _enqueue(queue, *, thread_id, turn_number=1):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="do the thing",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_signal_supersede_emits_cancelled(emitted):
    run = MagicMock()
    register_live_run(
        dispatch_id="cancel-live",
        thread_id="9001",
        source_repo="/repo",
        run=run,
    )
    try:
        mark = signal_supersede(
            dispatch_id="cancel-live",
            superseded_by="job-new",
            reason="same_thread_request_turn_2",
        )
    finally:
        unregister_live_run(dispatch_id="cancel-live")

    assert mark["method"] == "run_cancel"
    run.cancel.assert_called_once()
    assert len(emitted) == 1
    event = emitted[0]
    assert event.signal == "frontier.sdk.worker.cancelled"
    assert event.payload["dispatch_id"] == "cancel-live"
    assert event.payload["method"] == "run_cancel"
    assert event.payload["superseded_by"] == "job-new"
    assert event.payload["thread_id"] == "9001"
    assert event.payload["terminal_status"] == "cancelled"


def test_signal_supersede_bridge_abort_emits(emitted, monkeypatch):
    run = MagicMock()
    run.cancel.side_effect = RuntimeError("refused")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.abort_orphaned_bridge",
        lambda *, dispatch_id: True,
    )
    register_live_run(
        dispatch_id="cancel-abort",
        thread_id="9002",
        source_repo="/repo",
        run=run,
    )
    try:
        mark = signal_supersede(
            dispatch_id="cancel-abort",
            superseded_by="job-new",
            reason="test",
        )
    finally:
        unregister_live_run(dispatch_id="cancel-abort")

    assert mark["method"] == "bridge_abort"
    assert emitted[0].payload["method"] == "bridge_abort"
    assert "RuntimeError" in (emitted[0].payload.get("error") or "")


def test_escalate_supersede_abort_emits(emitted, monkeypatch):
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.abort_orphaned_bridge",
        lambda *, dispatch_id: True,
    )
    register_live_run(
        dispatch_id="cancel-escalate",
        thread_id="9003",
        source_repo="/repo",
        run=MagicMock(),
    )
    try:
        signal_supersede(
            dispatch_id="cancel-escalate",
            superseded_by="job-new",
            reason="grace_exhausted",
        )
        emitted.clear()
        assert escalate_supersede_abort(dispatch_id="cancel-escalate") is True
    finally:
        unregister_live_run(dispatch_id="cancel-escalate")

    assert len(emitted) == 1
    assert emitted[0].payload["method"] == "bridge_abort_escalate"
    assert emitted[0].payload["superseded_by"] == "job-new"


def test_pre_register_live_run_supersede_emits(emitted):
    import asyncio

    queue = AutoJobQueue()
    old = _enqueue(queue, thread_id="9004", turn_number=1)
    assert queue.claim_next().job_id == old.job_id
    new = _enqueue(queue, thread_id="9004", turn_number=2)

    evidence = asyncio.run(supersede_same_thread_inflight(new, queue=queue))
    auto_supersede._PENDING.clear()

    assert evidence["method"] == auto_supersede.PRE_REGISTER_LIVE_RUN
    assert len(emitted) == 1
    assert emitted[0].payload["method"] == "pre_register_live_run"
    assert emitted[0].payload["terminal_status"] == "displaced_pre_live"
    assert emitted[0].payload["dispatch_id"] == old.job_id
    assert emitted[0].payload["superseded_by"] == new.job_id


def test_operator_cancel_method_accepted(emitted) -> None:
    from services.git_integration_worker.cursor_sdk_cancel_events import (
        emit_sdk_worker_cancelled,
    )

    emit_sdk_worker_cancelled(
        dispatch_id="op-cancel-1",
        method="operator_cancel",
        reason="operator requested",
        thread_id="t-op",
    )
    assert len(emitted) == 1
    assert emitted[0].payload["method"] == "operator_cancel"
    assert emitted[0].payload["terminal_status"] == "cancelled"


def test_emit_operator_cancel_before_lease_released(emitted, monkeypatch) -> None:
    order: list[str] = []

    def _track_cancel(**kwargs: object) -> None:
        order.append("cancelled")
        from services.git_integration_worker.cursor_sdk_cancel_events import (
            FrontierSdkWorkerCancelled,
        )

        emitted.append(
            FrontierSdkWorkerCancelled(
                dispatch_id=str(kwargs["dispatch_id"]),
                method="operator_cancel",
                reason=str(kwargs["reason"]),
                thread_id=str(kwargs.get("thread_id") or ""),
            )
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_operator_cancel.emit_sdk_worker_cancelled",
        _track_cancel,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_operator_cancel.emit_write_lease_released",
        lambda **_: order.append("lease_released"),
    )
    from services.git_integration_worker.admission import WorkAdmissionController
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CancelDispatchResult,
    )
    from services.git_integration_worker.cursor_sdk_operator_cancel import (
        _emit_cancel_side_effects,
    )

    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test",
        pid=0,
        worker_started_at="test",
    )
    import asyncio

    asyncio.run(
        _emit_cancel_side_effects(
            result=CancelDispatchResult(
                outcome="cancelled",
                row={"thread_id": "t1"},
                lease_key="/repo",
                needs_promote=False,
            ),
            dispatch_id="op-1",
            cancel_reason="test",
            controller=controller,
            request=None,
        )
    )
    assert order.index("cancelled") < order.index("lease_released")
