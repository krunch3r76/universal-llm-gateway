"""``steer=cancel_discard`` / ``mode=discard`` park route (AC-CD-1..8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    reset_park_marks,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
    open_park_rows,
)
from services.git_integration_worker.cursor_sdk_park_preflight import (
    ParkRefusal,
    preflight_park,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.tests.test_cursor_sdk_park_cancel_resume_spike import (
    FakeCancellableRun,
)

_registered: list[str] = []


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_park_marks()
    yield
    for did in list(_registered):
        unregister_live_run(dispatch_id=did)
    _registered.clear()
    reset_park_marks()
    CursorDispatchLedger._instance = None


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []
    for mod in (
        "services.git_integration_worker.cursor_sdk_park_events",
        "services.git_integration_worker.cursor_sdk_cancel_events",
    ):
        monkeypatch.setattr(f"{mod}.emit_frontier_event", lambda ev: log.append(ev))
    return log


def _req(
    dispatch_id: str, *, thread_id: str = "t1", **overrides: Any
) -> CursorDispatchRequest:
    base: dict[str, Any] = {
        "thread_id": thread_id,
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": "work",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    dispatch_id: str,
    *,
    tmp_path: Path,
    thread_id: str = "t1",
    running: bool = False,
    sdk_agent_id: str | None = "agent-1",
    with_store: bool = True,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id, thread_id=thread_id)
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True, dispatch_id=dispatch_id, thread_id=thread_id, model_id="m"
        ),
        source_repo=str(tmp_path / "repo"),
        lease_key=str(tmp_path / f"lease-{dispatch_id}"),
    )
    if running:
        ledger.mark_running(dispatch_id=dispatch_id)
    if with_store and running:
        store = tmp_path / f"store-{dispatch_id}"
        store.mkdir(parents=True, exist_ok=True)
        (store / "index.db").write_text("x")
        ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    if sdk_agent_id and running:
        ledger.record_sdk_identity(
            dispatch_id=dispatch_id, agent_id=sdk_agent_id, run_id="r"
        )


def _row(dispatch_id: str) -> dict[str, Any]:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def _register(dispatch_id: str, *, thread_id: str, run: Any) -> None:
    register_live_run(
        dispatch_id=dispatch_id, thread_id=thread_id, source_repo="/repo", run=run
    )
    _registered.append(dispatch_id)


def test_preflight_discard_allows_idle_row(tmp_path: Path) -> None:
    _admit("idle-1", tmp_path=tmp_path, running=False)
    pre = preflight_park("idle-1", mode="discard")
    assert pre.refusal is None


def test_preflight_discard_skips_state_root_missing(tmp_path: Path) -> None:
    _admit("live-1", tmp_path=tmp_path, running=True, with_store=False)
    _register("live-1", thread_id="t1", run=FakeCancellableRun(id="r1", agent_id="a1"))
    pre = preflight_park("live-1", mode="discard")
    assert pre.refusal is None


def test_signal_discard_idle_returns_idle_discard(
    tmp_path: Path, events: list[Any]
) -> None:
    _admit("idle-2", tmp_path=tmp_path, running=False)
    result = signal_park(
        "idle-2",
        intent_id=None,
        drain_epoch=None,
        actor="operator",
        reason="mistaken admit",
        mode="discard",
    )
    assert result.idle_discard
    assert not result.requested
    assert [ev.signal for ev in events] == ["sdk.park.discard.requested"]


def test_signal_discard_live_cancels_run(tmp_path: Path, events: list[Any]) -> None:
    _admit("live-2", tmp_path=tmp_path, running=True)
    run = FakeCancellableRun(id="run-live-2", agent_id="agent-1")
    _register("live-2", thread_id="t1", run=run)
    result = signal_park(
        "live-2",
        intent_id=None,
        drain_epoch=None,
        actor="operator",
        reason="kill",
        mode="discard",
    )
    assert result.requested
    assert result.mode == "discard"
    assert run.cancel_calls == 1
    assert events[-1].payload["reason"] == "cancel_discard:operator"


@pytest.mark.asyncio
async def test_finalize_discard_idle_marks_ledger_and_no_resume_retain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_discard_idle,
    )

    _admit("idle-3", tmp_path=tmp_path, running=False)
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.bridge_death_harvest."
        "cortex_files_root",
        lambda: tmp_path / "cortex",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_prune."
        "maybe_prune_worktree_on_terminal",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition."
        "mark_lane_b_disposition_for_dispatch",
        lambda **_kw: None,
    )

    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at="b",
    )
    await finalize_discard_idle(
        dispatch_id="idle-3",
        thread_id="t1",
        execution_id="exec-idle-3",
        reply_to="cursor",
        source_repo=tmp_path / "repo",
        bus=bus,
        controller=controller,
        actor="operator",
        reason="void",
    )
    raw = _row("idle-3")
    assert raw["terminal_status"] == "cancelled"
    assert raw["park_kind"] == PARK_KIND_DISCARD
    rec = json.loads(raw["record_json"])
    assert not rec.get("resume_retain")
    assert open_park_rows() == []
    bus.terminate_dispatch.assert_awaited_once()


def test_mark_parked_discard_omits_resume_retain(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked

    _admit("d-disc", tmp_path=tmp_path, running=True)
    mark_parked(
        dispatch_id="d-disc",
        intent_id=None,
        drain_epoch=None,
        actor="op",
        reason="r",
        requested_at="2026-09-15T00:00:00+00:00",
        method="idle_discard",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
        park_kind=PARK_KIND_DISCARD,
    )
    rec = json.loads(_row("d-disc")["record_json"])
    assert "resume_retain" not in rec


def test_park_preflight_cancel_still_refuses_idle() -> None:
    pre = preflight_park("missing", mode="cancel")
    assert pre.refusal is ParkRefusal.NOT_FOUND


@pytest.mark.asyncio
async def test_release_claimed_auto_job_fails_one_id_park_still_404(
    tmp_path: Path, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release verb terminalizes one claimed auto job. cancel_discard stays 404
    when that id is not a park row. The verb does not SIGTERM GIW."""
    import os

    from services.git_integration_worker.cursor_auto.queue import (
        get_queue,
        reset_queue_for_tests,
    )
    from services.git_integration_worker.cursor_auto.release_claimed import (
        release_claimed_auto_job,
    )
    from services.git_integration_worker.cursor_sdk_park_http import park_one_dispatch

    killed: list[Any] = []
    belt: list[Any] = []
    monkeypatch.setattr(os, "kill", lambda *args, **kwargs: killed.append(args))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.auto_worker_loop.request_giw_belt_exit",
        lambda **kwargs: belt.append(kwargs),
    )
    reset_queue_for_tests(durable=False)
    queue = get_queue()

    def _enq(thread: str):
        return queue.enqueue(
            thread_id=thread,
            turn_number=1,
            subject=thread,
            body="TYPE: DIRECTIVE\n",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
        )

    target = _enq("release-target")
    other = _enq("release-other")
    claimed = queue.claim_job(target.job_id)
    kept = queue.claim_job(other.job_id)
    assert claimed is not None and kept is not None
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=2876571,
        worker_started_at="b",
    )
    result = release_claimed_auto_job(
        claimed.job_id,
        reason="a37026_release_claimed",
        queue=queue,
        controller=controller,
    )
    assert result["ok"] is True
    assert result["status"] == "failed"
    assert result["terminal_reason"] == "a37026_release_claimed"
    stored = queue.get(claimed.job_id)
    assert stored is not None and stored.status == "failed"
    assert kept.status == "claimed"
    ops = controller.active_ops()
    assert claimed.job_id not in {op.get("op_id") for op in ops}
    assert any(op.get("op_id") == kept.job_id for op in ops)
    status, body = await park_one_dispatch(
        dispatch_id=claimed.job_id,
        intent_id=None,
        drain_epoch=None,
        actor="operator",
        reason="cancel_discard",
        controller=controller,
        mode="discard",
    )
    assert status == 404
    assert body["code"] == "CURSOR_PARK_NOT_FOUND"
    assert killed == []
    assert belt == []


@pytest.mark.asyncio
async def test_release_route_409_until_override_then_failed(tmp_path: Path) -> None:
    """Fresh heartbeat refuses without override (409). Override terminalizes."""
    from fastapi.testclient import TestClient

    from services.git_integration_worker.app import create_app
    from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
    from services.git_integration_worker.cursor_auto.queue import (
        get_queue,
        reset_queue_for_tests,
    )

    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    queue = get_queue()
    job = queue.enqueue(
        thread_id="rel-409",
        turn_number=1,
        subject="fresh",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )
    claimed = queue.claim_job(job.job_id)
    assert claimed is not None
    app = create_app()
    app.state.admission_controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=1,
        worker_started_at="b",
    )
    client = TestClient(app)
    url = f"/api/v1/git/cursor-auto/jobs/{claimed.job_id}/release"
    refused = client.post(url, json={"reason": "too-soon"})
    assert refused.status_code == 409
    assert refused.json()["ok"] is not True
    released = client.post(url, json={"reason": "override-now", "override": True})
    assert released.status_code == 200
    assert released.json()["status"] == "failed"
    stored = queue.get(claimed.job_id)
    assert stored is not None and stored.status == "failed"


@pytest.mark.asyncio
async def test_release_route_409_when_present_but_not_claimed() -> None:
    from fastapi.testclient import TestClient

    from services.git_integration_worker.app import create_app
    from services.git_integration_worker.cursor_auto.queue import (
        get_queue,
        reset_queue_for_tests,
    )

    reset_queue_for_tests(durable=False)
    queue = get_queue()
    job = queue.enqueue(
        thread_id="rel-queued",
        turn_number=1,
        subject="queued",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )
    app = create_app()
    app.state.admission_controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=1,
        worker_started_at="b",
    )
    client = TestClient(app)
    present = client.post(
        f"/api/v1/git/cursor-auto/jobs/{job.job_id}/release",
        json={"reason": "not-claimed", "override": True},
    )
    assert present.status_code == 409
    assert present.json()["status"] == "queued"
    missing = client.post(
        "/api/v1/git/cursor-auto/jobs/does-not-exist/release",
        json={"reason": "missing"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_release_cancels_hop_and_concurrent_tracked_tasks() -> None:
    import asyncio

    from services.git_integration_worker.cursor_auto.queue import (
        get_queue,
        reset_queue_for_tests,
    )
    from services.git_integration_worker.cursor_auto.release_claimed import (
        release_claimed_auto_job,
    )

    reset_queue_for_tests(durable=False)
    queue = get_queue()
    job = queue.enqueue(
        thread_id="rel-tasks",
        turn_number=1,
        subject="tasks",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )
    claimed = queue.claim_job(job.job_id)
    assert claimed is not None
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=1,
        worker_started_at="b",
    )

    async def _hang() -> None:
        await asyncio.sleep(60)

    hop_op = f"cursor-auto-continuity-hop:{claimed.job_id}"
    concurrent_op = f"cursor-auto-concurrent:{claimed.job_id}"
    hop_task = controller.create_tracked_task(_hang(), op_id=hop_op)
    concurrent_task = controller.create_tracked_task(_hang(), op_id=concurrent_op)
    refused = release_claimed_auto_job(
        claimed.job_id,
        reason="alive",
        queue=queue,
        controller=controller,
        override=False,
    )
    assert refused["ok"] is not True
    assert queue.get(claimed.job_id).status == "claimed"
    released = release_claimed_auto_job(
        claimed.job_id,
        reason="override-alive",
        queue=queue,
        controller=controller,
        override=True,
    )
    assert released["status"] == "failed"
    assert set(released["cancelled_tasks"]) == {hop_op, concurrent_op}
    await asyncio.sleep(0)
    assert hop_task.cancelled()
    assert concurrent_task.cancelled()
