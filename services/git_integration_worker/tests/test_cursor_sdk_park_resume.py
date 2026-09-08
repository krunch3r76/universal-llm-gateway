"""Auto-resume of parked rows through the real admission path (AC-SR-7/8/13)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    SourceRefConflict,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    load_park_row,
    mark_parked,
    open_park_rows,
    park_projection,
)
from services.git_integration_worker.cursor_sdk_park_resume import (
    build_park_resume_request,
    render_park_resume_preamble,
    resume_parked_dispatches,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_WORK_KEY = "todo:steer-resume"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _admit_stubs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Admission must not need live Cursor auth, MCP parity, or a real bridge."""
    from services.git_integration_worker.cursor_sdk_context import (
        CursorApiKeyResolution,
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["user"], "mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "resolve_cursor_api_key",
        lambda *_a, **_k: CursorApiKeyResolution(provenance="env:CURSOR_API_KEY"),
    )
    monkeypatch.setattr(
        route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {"files": {}}
    )
    spawned = MagicMock(return_value=MagicMock(done=lambda: False))
    monkeypatch.setattr(WorkAdmissionController, "create_tracked_task", spawned)
    return spawned


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_events.emit_frontier_event",
        lambda ev: log.append(ev),
    )
    return log


def _bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 3})
    )
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at="b",
    )


def _seed_parked(
    dispatch_id: str,
    *,
    thread_id: str,
    tmp_path: Path,
    parked_offset_s: int = 0,
    expired: bool = False,
    conductor: bool = False,
    work_key: str | None = None,
) -> None:
    # Each parked mission owns its work_key: an open park row reserves it (D5.4b).
    work_key = work_key or f"{_WORK_KEY}-{dispatch_id}"
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message=f"packet body {dispatch_id}",
        handoff_contract="conductor" if conductor else "none",
        prompt_preamble="ORIGINAL PREAMBLE",
        skills=["reasoning-posture"],
        lane="A",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True, dispatch_id=dispatch_id, thread_id=thread_id, model_id="m"
        ),
        contract="conductor" if conductor else "none",
        source_repo=str(load_config().source_repo),
        lease_key=str(load_config().source_repo),
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared" if work_key else None,
        packet_kind="conductor" if conductor else None,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )
    if conductor:
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={"contract": "conductor", "packet_kind": "conductor"},
        )
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-r",
        drain_epoch=5,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=4,
        last_tool_calls=[{"tool_name": "fs", "status": "completed"}],
        sidecar_uri=f"cortex://notes/system/threads/{thread_id}-park-partial-{dispatch_id}.md",
    )
    parked_at = datetime.now(UTC) + timedelta(seconds=parked_offset_s)
    expires = (
        parked_at - timedelta(seconds=1) if expired else parked_at + timedelta(days=1)
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET parked_at=?, park_expires_at=? WHERE dispatch_id=?",
            (parked_at.isoformat(), expires.isoformat(), dispatch_id),
        )


def _row(dispatch_id: str) -> dict[str, Any] | None:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row is not None else None


# ------------------------------------------------------------------ AC-SR-7


@pytest.mark.asyncio
async def test_startup_resume_admits_open_rows_in_order_and_expires_stale(
    tmp_path: Path, events: list[Any], _admit_stubs: MagicMock
) -> None:
    _seed_parked("p-old", thread_id="7001", tmp_path=tmp_path, parked_offset_s=-100)
    _seed_parked("p-new", thread_id="7002", tmp_path=tmp_path, parked_offset_s=-10)
    _seed_parked(
        "p-exp", thread_id="7003", tmp_path=tmp_path, parked_offset_s=-500, expired=True
    )
    bus = _bus()

    summary = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="abc1234", bus=bus
    )

    assert summary.admitted == [("p-old", "p-old-r1"), ("p-new", "p-new-r1")]
    assert summary.expired == ["p-exp"]
    assert summary.refused == []
    for parent, child in summary.admitted:
        child_row = _row(child)
        assert child_row is not None
        assert child_row["resume_of"] == parent
        assert child_row["execution_id"] == f"exec-{parent}"
        assert child_row["thread_id"] == _row(parent)["thread_id"]
        assert child_row["work_key"] == f"{_WORK_KEY}-{parent}"
        # Same Lane-A lease: the second child queues behind the first (FIFO cap).
        assert child_row["status"] in ("admitted", "running", "queued")
        record = json.loads(child_row["record_json"])
        assert record["admitted_via"] == "giw_park_resume"
        assert record["lane"] == "A"
        assert record["skills"] == ["reasoning-posture"]
        assert record["prompt_preamble"].startswith("PARK-RESUME v1")
        assert "ORIGINAL PREAMBLE" in record["prompt_preamble"]
        assert record["message"] == f"packet body {parent}"
        assert _row(parent)["park_resumed_by"] == child
        assert park_projection(load_park_row(dispatch_id=parent))["state"] == "resumed"
    assert _admit_stubs.call_count == 1  # first child runs; second queues on the lease
    assert _row("p-new-r1")["status"] == "queued"
    resumed_turns = [
        c.kwargs for c in bus.reply.await_args_list if "RESUMED" in c.kwargs["subject"]
    ]
    assert [t["thread_id"] for t in resumed_turns] == ["7001", "7002"]
    assert resumed_turns[0]["subject"] == (
        "cursor-sdk dispatch p-old-r1 RESUMED (resume_of p-old, restart intent-r)"
    )
    # Expired row: event + link terminated (AC-SR-11) + untouched columns.
    exp = _row("p-exp")
    assert exp["park_resumed_by"] is None and exp["status"] == "cancelled"
    assert json.loads(exp["record_json"])["park"]["expired_at"]
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="7003", terminal_status="cancelled", execution_id="exec-p-exp"
    )
    signals = [ev.signal for ev in events]
    assert signals.count("sdk.park.resume_admitted") == 2
    assert signals.count("sdk.park.expired") == 1
    admitted_payloads = [
        ev.payload for ev in events if ev.signal == "sdk.park.resume_admitted"
    ]
    assert admitted_payloads[0]["code_version"] == "abc1234"
    assert open_park_rows() == [] or all(
        r.dispatch_id == "p-exp" for r in open_park_rows()
    )

    # Idempotent second pass: nothing re-admitted, expiry not re-emitted.
    again = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="abc1234", bus=bus
    )
    assert again.admitted == [] and again.expired == []
    assert [ev.signal for ev in events].count("sdk.park.expired") == 1


@pytest.mark.asyncio
async def test_conductor_resume_stamps_hop_successor(
    tmp_path: Path, events: list[Any]
) -> None:
    _seed_parked("p-cond", thread_id="7010", tmp_path=tmp_path, conductor=True)
    summary = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="v", bus=_bus()
    )
    assert summary.admitted == [("p-cond", "p-cond-r1")]
    record = json.loads(_row("p-cond")["record_json"])
    assert record["hop_successor"] == "p-cond-r1"
    child = json.loads(_row("p-cond-r1")["record_json"])
    assert child["handoff_contract"] == "conductor"


# ------------------------------------------------------------------ AC-SR-8


@pytest.mark.asyncio
async def test_refused_admission_keeps_row_open_then_admits_next_tick(
    tmp_path: Path, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    _seed_parked("p-ref", thread_id="7020", tmp_path=tmp_path)
    real_admit = route_mod.admit_cursor_dispatch

    async def _refuse(req: CursorDispatchRequest, **_kw: Any) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "CURSOR_WRITE_LEASE_HELD", "message": "held"},
        )

    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", _refuse)
    first = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="v", bus=_bus()
    )
    assert first.refused == [("p-ref", "CURSOR_WRITE_LEASE_HELD")]
    row = _row("p-ref")
    assert row["park_resumed_by"] is None
    park = json.loads(row["record_json"])["park"]
    assert park["resume_attempts"] == 1
    assert park["resume_refusals"][-1]["reason"] == "CURSOR_WRITE_LEASE_HELD"
    refused = [ev for ev in events if ev.signal == "sdk.park.resume_refused"]
    assert refused and refused[0].payload["attempt"] == 1

    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", real_admit)
    second = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="v", bus=_bus()
    )
    assert second.admitted == [("p-ref", "p-ref-r2")]
    assert _row("p-ref")["park_resumed_by"] == "p-ref-r2"


@pytest.mark.asyncio
async def test_existing_child_is_reconciled_not_readmitted(tmp_path: Path) -> None:
    _seed_parked("p-rec", thread_id="7030", tmp_path=tmp_path)
    first = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="v", bus=_bus()
    )
    assert first.admitted == [("p-rec", "p-rec-r1")]
    # Simulate a crash between admit and stamp: reopen the park.
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by=NULL WHERE dispatch_id='p-rec'"
        )
    second = await resume_parked_dispatches(
        cfg=load_config(), controller=_controller(), code_version="v", bus=_bus()
    )
    assert second.admitted == [] and second.reconciled == [("p-rec", "p-rec-r1")]
    assert _row("p-rec")["park_resumed_by"] == "p-rec-r1"


# ----------------------------------------------------------------- AC-SR-13


def test_open_park_row_reserves_work_key_but_child_is_exempt(tmp_path: Path) -> None:
    _seed_parked("p-wk", thread_id="7040", tmp_path=tmp_path, work_key=_WORK_KEY)
    ledger = CursorDispatchLedger.instance()
    foreign = CursorDispatchRequest(
        thread_id="7099",
        model="cursor/composer-2.5",
        dispatch_id="foreign-impl",
        execution_id="exec-foreign",
        message="steal the lineage",
        handoff_contract="implement",
    )
    with pytest.raises(SourceRefConflict) as excinfo:
        ledger.admit(
            req=foreign,
            fingerprint=ledger.fingerprint(foreign),
            execution_id=foreign.execution_id,
            caller_agent="cursor",
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id="foreign-impl",
                thread_id="7099",
                model_id="m",
            ),
            contract="implement",
            source_repo=str(tmp_path / "other"),
            lease_key=str(tmp_path / "other"),
            work_key=_WORK_KEY,
            identity_class="declared",
        )
    assert excinfo.value.holder_dispatch_id == "p-wk"
    assert _row("foreign-impl") is None

    child = build_park_resume_request(
        load_park_row(dispatch_id="p-wk"), attempt=1, code_version="v"
    )
    assert child.resume_of == "p-wk" and child.work_key == _WORK_KEY
    assert (
        ledger.admit(
            req=child,
            fingerprint=ledger.fingerprint(child),
            execution_id=child.execution_id,
            caller_agent="cursor",
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=child.dispatch_id,
                thread_id="7040",
                model_id="m",
            ),
            source_repo=str(load_config().source_repo),
            lease_key=str(load_config().source_repo),
            work_key=_WORK_KEY,
            identity_class="declared",
        )
        is None
    )


def test_preamble_and_request_builder_shapes(tmp_path: Path) -> None:
    _seed_parked("p-pre", thread_id="7050", tmp_path=tmp_path)
    row = load_park_row(dispatch_id="p-pre")
    assert row is not None
    text = render_park_resume_preamble(row, code_version="deadbee")
    assert text.startswith("PARK-RESUME v1 — substrate notice")
    assert "dispatch p-pre on agent-bus:7050" in text
    assert "restart intent intent-r (deploy)" in text
    assert "code_version deadbee" in text
    assert "cancelled after 4 tool calls; last tool calls: fs[completed]" in text
    assert (
        "Partial harvest: cortex://notes/system/threads/7050-park-partial-p-pre.md"
        in text
    )
    req = build_park_resume_request(row, attempt=3, code_version="deadbee")
    assert req.dispatch_id == "p-pre-r3"
    assert req.execution_id == "exec-p-pre"
    assert req.admitted_via == "giw_park_resume"
    assert req.prompt_preamble is not None and req.prompt_preamble.endswith(
        "ORIGINAL PREAMBLE"
    )
    assert req.lane == "A" and req.worktree_path is None
