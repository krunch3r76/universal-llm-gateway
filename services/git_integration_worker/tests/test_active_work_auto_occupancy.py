"""Claimed Auto occupancy must hold GIW drain idle (arc 9470).

Observed instance: job 8f95ca61-d46c-4b16-9a11-d8c9ba998703 (2026-08-18T14:14:03Z)
terminalized ``queue_owner_restart`` because ``/active-work`` / drain-state busy
was tickets ∪ live cursor-sdk dispatches only — a claimed in-process Auto
propagate was invisible, so the drain supervisor converged and SIGTERM beat
``post_terminal_status``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker import git_worker_drain_events as drain_events
from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.auto_worker_loop import (
    drain_blocks_new_auto_claims,
)
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)
    yield
    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    started: list[dict] = []
    completed: list[dict] = []
    monkeypatch.setattr(
        drain_events, "emit_drain_started", lambda **k: started.append(k)
    )
    monkeypatch.setattr(
        drain_events, "emit_drain_completed", lambda **k: completed.append(k)
    )
    return SimpleNamespace(started=started, completed=completed)


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=1234,
        worker_started_at="2026-01-01T00:00:00+00:00",
    )


def _claim_propagate_job():
    job = get_queue().enqueue(
        thread_id="9470",
        turn_number=1,
        subject="propagate giw",
        body="contract: propagate\n",
        from_agent="cursor-auto",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    claimed = get_queue().claim_next()
    assert claimed is not None
    assert claimed.job_id == job.job_id
    return claimed


def test_claimed_auto_job_makes_active_work_busy() -> None:
    """Idle-of-SDK + claimed Auto job ⇒ busy. Fails while Auto is omitted.

    Specimen: job 8f95ca61-d46c-4b16-9a11-d8c9ba998703 — top-level
    ``contract:propagate`` targeting git_integration_worker while GIW had no
    cursor-sdk/integrate work. Drain probed idle, SIGTERM, ``queue_owner_restart``.
    """
    claimed = _claim_propagate_job()
    app = create_app()
    app.state.admission_controller = _controller()
    client = TestClient(app)

    resp = client.get("/api/v1/git/active-work")
    assert resp.status_code == 200
    data = resp.json()
    assert data["busy"] is True
    assert data["active_count"] >= 1
    assert any(
        op.get("op_id") == claimed.job_id and op.get("kind") == "cursor-auto"
        for op in data["active_ops"]
    )

    drain = client.get("/api/v1/git/admin/drain-state")
    assert drain.status_code == 200
    snap = drain.json()
    assert snap["active_count"] >= 1
    assert any(op.get("op_id") == claimed.job_id for op in snap["active_ops"])


def test_claimed_auto_holds_drain_until_mark_done(events: SimpleNamespace) -> None:
    """begin_drain with a claimed Auto occupant must not emit drain.completed."""
    claimed = _claim_propagate_job()
    controller = _controller()
    epoch = controller.next_epoch()
    snap = controller.begin_drain(reason="r", intent_id="i-auto", drain_epoch=epoch)
    assert snap["active_count"] >= 1
    assert events.completed == []
    assert any(op.get("op_id") == claimed.job_id for op in controller.active_ops())

    get_queue().mark_done(claimed.job_id)
    controller.recheck_drain_idle()
    assert controller.active_count() == 0
    assert len(events.completed) == 1


def test_queued_auto_job_does_not_make_busy() -> None:
    """Queued-not-claimed Auto work is not executing; drain may still converge."""
    get_queue().enqueue(
        thread_id="9470",
        turn_number=1,
        subject="queued only",
        body="contract: propagate\n",
        from_agent="cursor-auto",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    controller = _controller()
    assert controller.active_count() == 0
    assert controller.active_ops() == []


def test_drain_blocks_new_auto_claims() -> None:
    """New Auto claims must stop while draining so occupancy can reach zero."""
    controller = _controller()
    assert drain_blocks_new_auto_claims(None) is False
    assert drain_blocks_new_auto_claims(controller) is False
    controller.begin_drain(
        reason="r", intent_id="i-block", drain_epoch=controller.next_epoch()
    )
    assert drain_blocks_new_auto_claims(controller) is True
