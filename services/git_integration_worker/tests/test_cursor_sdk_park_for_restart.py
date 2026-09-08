"""Park ladder, D3 refusals, sweep idempotency, bridge convergence (AC-SR-1..4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_converge import (
    converge_bridges_after_park,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    park_mark,
    reset_park_marks,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    load_park_row,
    mark_parked,
    open_park_rows,
    park_projection,
)
from services.git_integration_worker.cursor_sdk_park_preflight import (
    REFUSAL_HTTP,
    ParkRefusal,
    preflight_park,
)
from services.git_integration_worker.cursor_sdk_park_sweep import (
    park_for_restart_sweep,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    signal_supersede,
    unregister_live_run,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.tests.test_cursor_sdk_park_cancel_resume_spike import (
    FakeCancellableRun,
)

_INTENT = "intent-park-1"


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


_registered: list[str] = []


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []
    for mod in (
        "services.git_integration_worker.cursor_sdk_park_events",
        "services.git_integration_worker.cursor_sdk_cancel_events",
    ):
        monkeypatch.setattr(f"{mod}.emit_frontier_event", lambda ev: log.append(ev))
    return log


def _signals(events: list[Any]) -> list[str]:
    return [ev.signal for ev in events]


def _req(dispatch_id: str, *, thread_id: str, **overrides: Any) -> CursorDispatchRequest:
    base: dict[str, Any] = {
        "thread_id": thread_id,
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": f"exec-{dispatch_id}",
        "message": f"work {dispatch_id}",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_running(
    dispatch_id: str,
    *,
    thread_id: str,
    tmp_path: Path,
    sdk_agent_id: str | None = "agent-1",
    with_store: bool = True,
    work_key: str | None = None,
    lane: str | None = None,
    nest_under: str | None = None,
    read_only: bool = False,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id, thread_id=thread_id, lane=lane, nest_under=nest_under)
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
        read_only=read_only,
        work_key=work_key,
        identity_class="declared" if work_key else None,
        nest_under=nest_under,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    if with_store:
        store = tmp_path / f"store-{dispatch_id}"
        store.mkdir(parents=True, exist_ok=True)
        (store / "index.db").write_text("x")
        ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(dispatch_id=dispatch_id, agent_id=sdk_agent_id, run_id="r")


def _register(dispatch_id: str, *, thread_id: str, run: Any) -> None:
    register_live_run(
        dispatch_id=dispatch_id, thread_id=thread_id, source_repo="/repo", run=run
    )
    _registered.append(dispatch_id)


def _row(dispatch_id: str) -> dict[str, Any]:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()}


# ------------------------------------------------------------------ AC-SR-1


def test_signal_park_cancels_live_run_without_lane_b_disposition(
    tmp_path: Path, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    disposition_calls: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition."
        "mark_lane_b_disposition_for_dispatch",
        lambda **kw: disposition_calls.append(kw["dispatch_id"]),
    )
    _admit_running("d1", thread_id="t1", tmp_path=tmp_path)
    run = FakeCancellableRun(id="run-d1", agent_id="agent-1")
    _register("d1", thread_id="t1", run=run)

    result = signal_park(
        "d1", intent_id=_INTENT, drain_epoch=3, actor="manage", reason="deploy"
    )

    assert result.requested
    assert result.method == "run_cancel"
    assert run.cancel_calls == 1
    mark = park_mark("d1")
    assert mark is not None and mark.intent_id == _INTENT and mark.drain_epoch == 3
    assert _row("d1")["status"] == "running", "park mark alone must not mutate the row"
    assert _signals(events) == ["sdk.park.requested", "frontier.sdk.worker.cancelled"]
    cancelled = events[-1].payload
    assert cancelled["method"] == "run_cancel"
    assert cancelled["reason"] == f"park_for_restart:{_INTENT}"
    assert disposition_calls == []
    # Idempotent re-signal while the mark is live: no second cancel RPC.
    again = signal_park("d1", intent_id=_INTENT, drain_epoch=3, actor="manage", reason="x")
    assert again.requested and run.cancel_calls == 1


def test_mark_parked_projection_and_open_rows(tmp_path: Path) -> None:
    _admit_running("d2", thread_id="t2", tmp_path=tmp_path)
    row = mark_parked(
        dispatch_id="d2",
        intent_id=_INTENT,
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-09-08T00:00:00+00:00",
        method="run_cancel",
        tool_call_count=12,
        last_tool_calls=[{"tool_name": "fs", "status": "completed"}],
        sidecar_uri="cortex://notes/system/threads/t2-park-partial-d2.md",
    )
    assert row is not None
    raw = _row("d2")
    assert raw["status"] == "cancelled" and raw["terminal_status"] == "cancelled"
    assert raw["park_kind"] == "park_for_restart"
    assert raw["park_intent_id"] == _INTENT
    assert raw["parked_at"] and raw["park_expires_at"] > raw["parked_at"]
    record = json.loads(raw["record_json"])
    assert record["resume_retain"] is True
    assert record["park"]["method"] == "run_cancel"
    assert record["park"]["tool_call_count"] == 12
    projection = park_projection(load_park_row(dispatch_id="d2"))
    assert projection is not None and projection["state"] == "parked"
    assert [r.dispatch_id for r in open_park_rows()] == ["d2"]


# ------------------------------------------------------------------ AC-SR-2


def test_refusal_precedence_each_code_and_no_mutation(
    tmp_path: Path, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = CursorDispatchLedger.instance()

    # 1 NOT_FOUND
    r = signal_park("nope", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.NOT_FOUND and REFUSAL_HTTP[r.refusal] == (404, False)

    # 2 ALREADY_TERMINAL (+ already_parked for same intent)
    _admit_running("term", thread_id="tt", tmp_path=tmp_path)
    ledger.mark_terminal(dispatch_id="term", terminal_status="completed")
    r = signal_park("term", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.ALREADY_TERMINAL and not r.already_parked
    _admit_running("parked", thread_id="tp", tmp_path=tmp_path)
    mark_parked(
        dispatch_id="parked",
        intent_id=_INTENT,
        drain_epoch=1,
        actor="a",
        reason="r",
        requested_at="x",
        method="run_cancel",
        tool_call_count=1,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    r = signal_park("parked", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.ALREADY_TERMINAL and r.already_parked
    r = signal_park("parked", intent_id="other", drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.ALREADY_TERMINAL and not r.already_parked

    # 3 NOT_LIVE_HERE (running row, no bridge run in this process)
    _admit_running("orphan", thread_id="to", tmp_path=tmp_path)
    r = signal_park("orphan", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.NOT_LIVE_HERE and REFUSAL_HTTP[r.refusal][0] == 409
    assert _row("orphan")["status"] == "running"

    # 4 SUPERSEDE_IN_FLIGHT
    _admit_running("sup", thread_id="ts", tmp_path=tmp_path)
    sup_run = FakeCancellableRun(id="run-sup")
    _register("sup", thread_id="ts", run=sup_run)
    signal_supersede(dispatch_id="sup", superseded_by="newer", reason="test")
    r = signal_park("sup", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.SUPERSEDE_IN_FLIGHT

    # 5 NEST_CHAIN — parked_waiting parent, and the live child under it
    _admit_running("parent", thread_id="tn", tmp_path=tmp_path)
    _register("parent", thread_id="tn", run=FakeCancellableRun(id="run-parent"))
    assert ledger.park_for_nested(parent_id="parent", child_id="child")
    r = signal_park("parent", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.NEST_CHAIN and REFUSAL_HTTP[r.refusal][0] == 422
    _admit_running("child", thread_id="tn-child", tmp_path=tmp_path)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET nest_under='parent' WHERE dispatch_id='child'"
        )
    _register("child", thread_id="tn-child", run=FakeCancellableRun(id="run-child"))
    r = signal_park("child", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.NEST_CHAIN

    # 6 NOT_RESUMABLE_YET (retryable) → succeeds after record_sdk_identity
    _admit_running("young", thread_id="ty", tmp_path=tmp_path, sdk_agent_id=None)
    young_run = FakeCancellableRun(id="run-young")
    _register("young", thread_id="ty", run=young_run)
    r = signal_park("young", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.NOT_RESUMABLE_YET and REFUSAL_HTTP[r.refusal] == (409, True)
    assert young_run.cancel_calls == 0
    ledger.record_sdk_identity(dispatch_id="young", agent_id="agent-y", run_id="r")
    r = signal_park("young", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.requested and young_run.cancel_calls == 1

    # 7 STATE_ROOT_MISSING
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    _admit_running("nostore", thread_id="tns", tmp_path=tmp_path, with_store=False)
    _register("nostore", thread_id="tns", run=FakeCancellableRun(id="run-ns"))
    r = signal_park("nostore", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.STATE_ROOT_MISSING and REFUSAL_HTTP[r.refusal][0] == 422

    # 8 LANE_B_UNPINNED (lane B record, no active pin)
    _admit_running("laneb", thread_id="tb", tmp_path=tmp_path, lane="B")
    _register("laneb", thread_id="tb", run=FakeCancellableRun(id="run-b"))
    r = signal_park("laneb", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.LANE_B_UNPINNED

    # 9 CANCEL_FAILED — cancel raises and bridge abort is unavailable
    _admit_running("stuck", thread_id="tk", tmp_path=tmp_path)
    stuck_run = FakeCancellableRun(id="run-stuck")
    stuck_run.cancel = lambda: (_ for _ in ()).throw(RuntimeError("bridge gone"))  # type: ignore[method-assign]
    _register("stuck", thread_id="tk", run=stuck_run)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_for_restart.abort_orphaned_bridge",
        lambda *, dispatch_id: False,
    )
    r = signal_park("stuck", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.CANCEL_FAILED and REFUSAL_HTTP[r.refusal][0] == 503
    assert park_mark("stuck") is None and _row("stuck")["status"] == "running"

    # 10 RUN_ALREADY_TERMINAL — run finished, thread not yet unwound
    _admit_running("done", thread_id="td", tmp_path=tmp_path)
    done_run = FakeCancellableRun(id="run-done")
    done_run.finish()
    _register("done", thread_id="td", run=done_run)
    r = signal_park("done", intent_id=_INTENT, drain_epoch=1, actor="a", reason="r")
    assert r.refusal is ParkRefusal.RUN_ALREADY_TERMINAL and done_run.cancel_calls == 0

    refused = [ev for ev in events if ev.signal == "sdk.park.refused"]
    codes = {ev.payload["refusal"] for ev in refused}
    assert codes == {
        "NOT_FOUND",
        "ALREADY_TERMINAL",
        "NOT_LIVE_HERE",
        "SUPERSEDE_IN_FLIGHT",
        "NEST_CHAIN",
        "NOT_RESUMABLE_YET",
        "STATE_ROOT_MISSING",
        "LANE_B_UNPINNED",
        "CANCEL_FAILED",
        "RUN_ALREADY_TERMINAL",
    }
    # already_parked is an idempotent 200, not a refusal event
    assert all(ev.payload["dispatch_id"] != "parked" or ev.payload["refusal"] for ev in refused)


def test_preflight_orders_not_live_before_nest_chain(tmp_path: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_running("pw", thread_id="tpw", tmp_path=tmp_path)
    assert ledger.park_for_nested(parent_id="pw", child_id="c")
    assert preflight_park("pw").refusal is ParkRefusal.NOT_LIVE_HERE


# ------------------------------------------------------------------ AC-SR-3


def test_sweep_requests_live_refuses_nested_and_orphan_then_idempotent(
    tmp_path: Path, events: list[Any]
) -> None:
    ledger = CursorDispatchLedger.instance()
    runs: dict[str, FakeCancellableRun] = {}
    for did in ("l1", "l2", "l3"):
        _admit_running(did, thread_id=f"t-{did}", tmp_path=tmp_path)
        runs[did] = FakeCancellableRun(id=f"run-{did}")
        _register(did, thread_id=f"t-{did}", run=runs[did])
    _admit_running("np", thread_id="t-np", tmp_path=tmp_path)
    _register("np", thread_id="t-np", run=FakeCancellableRun(id="run-np"))
    assert ledger.park_for_nested(parent_id="np", child_id="nc")
    _admit_running("nc", thread_id="t-nc", tmp_path=tmp_path)
    with ledger._connect() as conn:
        conn.execute("UPDATE cursor_sdk_dispatches SET nest_under='np' WHERE dispatch_id='nc'")
    _register("nc", thread_id="t-nc", run=FakeCancellableRun(id="run-nc"))
    _admit_running("orphan", thread_id="t-o", tmp_path=tmp_path)

    summary = park_for_restart_sweep(
        intent_id=_INTENT, drain_epoch=4, actor="manage", reason="deploy"
    )

    assert sorted(summary.requested) == ["l1", "l2", "l3"]
    refusals = {r["dispatch_id"]: r["refusal"] for r in summary.refused}
    assert refusals == {"nc": "NEST_CHAIN", "orphan": "NOT_LIVE_HERE"}
    assert summary.live_after == 1  # NEST_CHAIN blocks; NOT_LIVE_HERE self-clears
    assert all(runs[d].cancel_calls == 1 for d in runs)
    sweep_events = [ev for ev in events if ev.signal == "sdk.park.sweep"]
    assert sweep_events and sweep_events[-1].payload["requested"] == summary.requested

    for did in ("l1", "l2", "l3"):
        mark_parked(
            dispatch_id=did,
            intent_id=_INTENT,
            drain_epoch=4,
            actor="manage",
            reason="deploy",
            requested_at="x",
            method="run_cancel",
            tool_call_count=3,
            last_tool_calls=[],
            sidecar_uri=None,
        )
        unregister_live_run(dispatch_id=did)
    again = park_for_restart_sweep(
        intent_id=_INTENT, drain_epoch=4, actor="manage", reason="deploy"
    )
    assert again.requested == []
    assert sorted(again.already_parked) == ["l1", "l2", "l3"]
    assert all(runs[d].cancel_calls == 1 for d in runs)


# ------------------------------------------------------------------ AC-SR-4


def test_converge_recheck_emits_drain_completed_without_sigterm(
    monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(), worker_id="w", pid=1, worker_started_at="b"
    )
    drain_events: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.admission.drain_events.emit_drain_started",
        lambda **kw: drain_events.append("started"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.admission.drain_events.emit_drain_completed",
        lambda **kw: drain_events.append("completed"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_restart_bridge_gate."
        "defer_restart_for_live_bridges",
        lambda **kw: False,
    )
    ticket = controller.try_admit("cursor_sdk", op_id="d-live", route="/x")
    controller.begin_drain(reason="test", intent_id=_INTENT, drain_epoch=1)
    assert drain_events == ["started"]
    # Ticket closed while the bridge process still lingers: no completion yet.
    controller.close_ticket(ticket.op_id, terminal_status="cancelled")
    counts = iter([2, 1, 0])

    aborted = asyncio.run(
        converge_bridges_after_park(
            parked_ids=["d-live"],
            intent_id=_INTENT,
            controller=controller,
            count_bridges=lambda: next(counts),
            idle_budget_s=5.0,
            poll_s=0.01,
        )
    )
    assert aborted == 0
    assert drain_events == ["started", "completed"]


def test_converge_aborts_only_lingering_parked_bridges(
    monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    from services.git_integration_worker import cursor_sdk_orphan as orphan_mod

    orphan_mod._active_clients.clear()
    orphan_mod._active_clients["parked-1"] = object()  # type: ignore[assignment]
    orphan_mod._active_clients["foreign"] = object()  # type: ignore[assignment]
    aborted_ids: list[str] = []

    def _abort(*, dispatch_id: str) -> bool:
        aborted_ids.append(dispatch_id)
        orphan_mod._active_clients.pop(dispatch_id, None)
        return True

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_converge.abort_orphaned_bridge",
        _abort,
    )
    rechecks: list[bool] = []
    controller = type("C", (), {"recheck_drain_idle": lambda self: rechecks.append(True)})()
    try:
        aborted = asyncio.run(
            converge_bridges_after_park(
                parked_ids=["parked-1"],
                intent_id=_INTENT,
                controller=controller,
                count_bridges=lambda: len(orphan_mod._active_clients),
                idle_budget_s=0.02,
                poll_s=0.01,
            )
        )
    finally:
        orphan_mod._active_clients.clear()
    assert aborted == 1 and aborted_ids == ["parked-1"]
    assert rechecks == [True]
    assert [ev.payload["dispatch_id"] for ev in events if ev.signal == "sdk.park.bridge_abort_escalated"] == ["parked-1"]
