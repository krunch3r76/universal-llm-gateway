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
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    reset_park_marks,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
    load_park_row,
    mark_parked,
    open_park_rows,
    park_projection,
)
from services.git_integration_worker.cursor_sdk_park_preflight import ParkRefusal
from services.git_integration_worker.cursor_sdk_park_resume import (
    RESUME_REFUSAL_SAME_PROCESS,
    build_park_resume_request,
    process_started_after_park,
    render_park_resume_preamble,
    resume_parked_dispatches,
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


def _controller(*, started_at: str | None = None) -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at=started_at or datetime.now(UTC).isoformat(),
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
    park_kind: str = "park_for_restart",
    finalize: bool = True,
    omit_sdk_agent_id: bool = False,
    with_store: bool = True,
    lease_key: str | None = None,
    reason: str = "deploy",
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
        lease_key=lease_key or str(load_config().source_repo),
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared" if work_key else None,
        packet_kind="conductor" if conductor else None,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x")
    if with_store:
        ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id,
        agent_id=None if omit_sdk_agent_id else f"agent-{dispatch_id}",
        run_id="r",
    )
    if conductor:
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={"contract": "conductor", "packet_kind": "conductor"},
        )
    if not finalize:
        return
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-r",
        drain_epoch=5,
        actor="manage",
        reason=reason,
        requested_at="x",
        method="run_cancel",
        tool_call_count=4,
        last_tool_calls=[{"tool_name": "fs", "status": "completed"}],
        sidecar_uri=f"cortex://notes/system/threads/{thread_id}-park-partial-{dispatch_id}.md",
        park_kind=park_kind,
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


def _admit_on_key(
    req: CursorDispatchRequest,
    *,
    work_key: str,
    caller_agent: str = "cursor",
    contract: str = "implement",
) -> None:
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=caller_agent,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="m",
        ),
        contract=contract,
        source_repo=str(load_config().source_repo),
        lease_key=f"lease-{req.dispatch_id}",
        work_key=work_key,
        identity_class="declared",
    )


def _mark_completed(dispatch_id: str) -> None:
    """Drop a child out of the active-status set so only the park can hold the key."""
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='completed', "
            "terminal_status='completed', terminal_at=? WHERE dispatch_id=?",
            (datetime.now(UTC).isoformat(), dispatch_id),
        )


# ------------------------------------------------------------------ AC-SR-7


def test_process_started_after_park_specimen_11667() -> None:
    """Drain-cancel on the parking process is not a restart (11667#770)."""
    assert (
        process_started_after_park(
            "2026-09-20T05:02:17.804702+00:00",
            "2026-09-20T05:16:09.239336+00:00",
        )
        is False
    )
    assert (
        process_started_after_park(
            "2026-09-20T05:40:00+00:00",
            "2026-09-20T05:16:09.239336+00:00",
        )
        is True
    )
    assert process_started_after_park("b", "2026-09-20T05:16:09+00:00") is False


@pytest.mark.asyncio
async def test_same_process_park_is_not_auto_resumed(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    _seed_parked("p-live", thread_id="11667", tmp_path=tmp_path, parked_offset_s=0)
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await resume_parked_dispatches(
        cfg=load_config(),
        controller=_controller(started_at=started),
        code_version="6e1cd6755",
        bus=_bus(),
    )
    assert summary.admitted == []
    assert summary.refused == [("p-live", RESUME_REFUSAL_SAME_PROCESS)]
    assert park_projection(load_park_row(dispatch_id="p-live"))["state"] == "parked"


@pytest.mark.asyncio
async def test_density_harness_park_resumes_on_same_process(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """a:37450 — ignored-steer is not a restart; same-process refuse strands it."""
    _seed_parked(
        "p-density",
        thread_id="14724",
        tmp_path=tmp_path,
        parked_offset_s=0,
        reason="density-harness-ignored-steer",
    )
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await resume_parked_dispatches(
        cfg=load_config(),
        controller=_controller(started_at=started),
        code_version="6e1cd6755",
        bus=_bus(),
    )
    assert summary.refused == []
    assert summary.admitted == [("p-density", "p-density-r1")]


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
        # Same lease key: the second child queues behind the first (FIFO cap).
        assert child_row["status"] in ("admitted", "running", "queued")
        record = json.loads(child_row["record_json"])
        assert record["admitted_via"] == "giw_park_resume"
        # select_lane assigns A after the explicit-lane gate.
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


def test_hand_resume_of_open_park_releases_work_key(tmp_path: Path) -> None:
    """A caller ``resume_of`` of an open park closes the work-key hold.

    GIW auto-resume stamps ``park_resumed_by`` after admit. A hand admit
    (web-anthropic ``resume_of``) did not, so the cancelled parent stayed the
    holder and a later plain admit 409'd until ``cancel_discard``.
    """
    _seed_parked("park-a", thread_id="7100", tmp_path=tmp_path, work_key=_WORK_KEY)
    assert _row("park-a")["status"] == "cancelled"
    assert _row("park-a")["park_resumed_by"] is None
    hand = CursorDispatchRequest(
        thread_id="7101",
        model="cursor/composer-2.5",
        dispatch_id="hand-b",
        execution_id="exec-hand-b",
        caller_agent="web-anthropic",
        message="hand resume of the parked lineage",
        handoff_contract="implement",
        resume_of="park-a",
    )
    _admit_on_key(hand, work_key=_WORK_KEY, caller_agent="web-anthropic")
    assert _row("hand-b")["resume_of"] == "park-a"
    _mark_completed("hand-b")
    plain = CursorDispatchRequest(
        thread_id="7102",
        model="cursor/composer-2.5",
        dispatch_id="plain-c",
        execution_id="exec-plain-c",
        message="plain admit after the park was resumed",
        handoff_contract="implement",
    )
    _admit_on_key(plain, work_key=_WORK_KEY)
    assert _row("park-a")["park_resumed_by"] == "hand-b"
    assert _row("plain-c") is not None


def test_peer_ignores_park_already_named_by_resume_of(tmp_path: Path) -> None:
    """A park row some child already resumes is not the work-key holder.

    Historical rows can have ``resume_of`` set and ``park_resumed_by`` still
    NULL (the stamp lived only on the GIW path). The peer predicate has to
    ignore that parent even when the column was never written.
    """
    _seed_parked("park-old", thread_id="7110", tmp_path=tmp_path, work_key=_WORK_KEY)
    prior = CursorDispatchRequest(
        thread_id="7111",
        model="cursor/composer-2.5",
        dispatch_id="prior-child",
        execution_id="exec-prior-child",
        caller_agent="web-anthropic",
        message="resume that never stamped park_resumed_by",
        handoff_contract="implement",
        resume_of="park-old",
    )
    _admit_on_key(prior, work_key=_WORK_KEY, caller_agent="web-anthropic")
    _mark_completed("prior-child")
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by=NULL "
            "WHERE dispatch_id='park-old'"
        )
    assert _row("park-old")["park_resumed_by"] is None
    assert _row("prior-child")["resume_of"] == "park-old"
    plain = CursorDispatchRequest(
        thread_id="7112",
        model="cursor/composer-2.5",
        dispatch_id="plain-after",
        execution_id="exec-plain-after",
        message="plain admit must not see the resumed park as holder",
        handoff_contract="implement",
    )
    _admit_on_key(plain, work_key=_WORK_KEY)
    assert _row("plain-after") is not None


def test_giw_park_resume_admit_leaves_stamp_to_mark_park_resumed(
    tmp_path: Path,
) -> None:
    """GIW auto-resume still stamps only after its route returns success.

    ``ledger.admit`` of a ``giw_park_resume`` child must not write
    ``park_resumed_by``. A post-admit refusal (drain) would otherwise close
    the park onto a child the route did not accept.
    """
    _seed_parked("p-giw", thread_id="7120", tmp_path=tmp_path, work_key=_WORK_KEY)
    child = build_park_resume_request(
        load_park_row(dispatch_id="p-giw"), attempt=1, code_version="v"
    )
    assert child.admitted_via == "giw_park_resume"
    _admit_on_key(
        child, work_key=_WORK_KEY, caller_agent=child.caller_agent or "cursor"
    )
    assert _row(child.dispatch_id) is not None
    assert _row(child.dispatch_id)["resume_of"] == "p-giw"
    assert _row("p-giw")["park_resumed_by"] is None


def test_cancel_discard_does_not_reserve_the_work_key(tmp_path: Path) -> None:
    _seed_parked(
        "p-disc",
        thread_id="7060",
        tmp_path=tmp_path,
        work_key=_WORK_KEY,
        park_kind=PARK_KIND_DISCARD,
    )
    ledger = CursorDispatchLedger.instance()
    nxt = CursorDispatchRequest(
        thread_id="7061",
        model="cursor/composer-2.5",
        dispatch_id="after-discard",
        execution_id="exec-after-discard",
        message="the discarded pin is free",
        handoff_contract="conductor",
    )
    ledger.admit(
        req=nxt,
        fingerprint=ledger.fingerprint(nxt),
        execution_id=nxt.execution_id,
        caller_agent="liaison-ticker",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="after-discard",
            thread_id="7061",
            model_id="m",
        ),
        contract="conductor",
        source_repo=str(tmp_path / "repo"),
        lease_key=str(tmp_path / "repo"),
        work_key=_WORK_KEY,
        identity_class="declared",
    )
    assert _row("after-discard") is not None


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
    assert req.lane is None and req.worktree_path is None
    assert req.workspace is None


def test_park_resume_builder_copies_record_workspace(tmp_path: Path) -> None:
    _seed_parked("p-ws", thread_id="37504", tmp_path=tmp_path)
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id="p-ws", patch={"workspace": "cryptax"}
    )
    row = load_park_row(dispatch_id="p-ws")
    assert row is not None
    req = build_park_resume_request(row, attempt=1, code_version="v")
    assert req.workspace == "cryptax"


def test_park_resume_builder_derives_workspace_from_source_repo(
    tmp_path: Path,
) -> None:
    """Legacy park rows omit record_json.workspace; pin still needs the satellite."""
    _seed_parked("p-legacy", thread_id="37504b", tmp_path=tmp_path)
    sat = tmp_path / "cryptax"
    sat.mkdir()
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_repo=? WHERE dispatch_id='p-legacy'",
            (str(sat),),
        )
    row = load_park_row(dispatch_id="p-legacy")
    assert row is not None
    assert row.source_repo == str(sat)
    req = build_park_resume_request(row, attempt=1, code_version="v")
    assert req.workspace == "cryptax"


def test_park_resume_builder_omits_renamed_hub(tmp_path: Path) -> None:
    """a:37530 — hub-parent auto-resume must not yield the install directory name."""
    hub = tmp_path / "ulg-install"
    hub.mkdir()
    _seed_parked("p-renamed-hub", thread_id="37530", tmp_path=tmp_path)
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_repo=? "
            "WHERE dispatch_id='p-renamed-hub'",
            (str(hub),),
        )
    row = load_park_row(dispatch_id="p-renamed-hub")
    assert row is not None
    req = build_park_resume_request(row, attempt=1, code_version="v", hub=hub)
    assert req.workspace is None


def test_parent_row_recorded_workspace_is_what_admit_inherits(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_sdk_satellite_workspace import (
        recorded_workspace,
    )
    from services.git_integration_worker.cursor_sdk_store_locus import load_parent_row

    _seed_parked("p-inherit", thread_id="37504c", tmp_path=tmp_path)
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id="p-inherit", patch={"workspace": "cryptax"}
    )
    parent = load_parent_row(CursorDispatchLedger.instance(), parent_id="p-inherit")
    assert parent is not None
    assert (
        recorded_workspace(
            record_json=parent.record_json,
            source_repo=parent.source_repo,
            hub=load_config().source_repo,
        )
        == "cryptax"
    )


def _init_git(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "master", str(path)], check=True, capture_output=True
    )
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(
            ["git", "-C", str(path), "config", key, value],
            check=True,
            capture_output=True,
        )
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_resume_of_omitted_workspace_pins_satellite(
    tmp_path: Path, _admit_stubs: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37506 — admit inherit must reach pin with the satellite repo (14750#2)."""
    import subprocess

    from services.git_integration_worker.config import WorkerConfig
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    projects = tmp_path / "projects"
    hub = projects / "hub"
    satellite = projects / "cryptax"
    _init_git(hub)
    _init_git(satellite)
    roster = hub / "cursor-plugins/ulg-ecosystem/SATELLITES.txt"
    roster.parent.mkdir(parents=True)
    roster.write_text("cryptax\n", encoding="utf-8")
    wt = tmp_path / "lane-14724"
    tip = subprocess.run(
        ["git", "-C", str(satellite), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(satellite), "branch", "lane-14724", tip],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(satellite), "worktree", "add", str(wt), "lane-14724"],
        check=True,
        capture_output=True,
    )

    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace=projects,
        green_gate_cmd=["true"],
    )
    pins: list[dict[str, object]] = []

    def _pin(**kwargs: object) -> str:
        pins.append(kwargs)
        return "ulg:lock"

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.pin_lane_worktree_on_admit",
        _pin,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_posture."
        "b_worktree_materialized",
        lambda **_k: True,
    )

    ledger = CursorDispatchLedger.instance()
    parent = CursorDispatchRequest(
        thread_id="14724",
        model="cursor/composer-2.5",
        dispatch_id="p-sat-parent",
        execution_id="exec-p-sat-parent",
        caller_agent="cursor",
        message="satellite parent",
        handoff_contract="conductor",
        workspace="cryptax",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(wt),
        work_key="todo:cryptax-p5-csv-refresh",
    )
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=parent.dispatch_id,
            thread_id=parent.thread_id,
            model_id="m",
        ),
        contract="conductor",
        source_repo=str(satellite.resolve()),
        lease_key=str(wt.resolve()),
        work_key=parent.work_key,
        source_ref=parent.work_key,
        identity_class="declared",
    )
    store = tmp_path / "store-p-sat-parent"
    store.mkdir(parents=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=parent.dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=parent.dispatch_id, agent_id="agent-p-sat-parent", run_id="r"
    )
    ledger.mark_running(dispatch_id=parent.dispatch_id)
    mark_parked(
        dispatch_id=parent.dispatch_id,
        intent_id="intent-37504",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=1,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    child = CursorDispatchRequest(
        thread_id="14724",
        model="cursor/composer-2.5",
        dispatch_id="p-sat-child",
        execution_id="exec-p-sat-child",
        caller_agent="cursor",
        message="resume without workspace",
        handoff_contract="conductor",
        resume_of="p-sat-parent",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(wt),
        work_key="todo:cryptax-p5-csv-refresh",
    )
    resp = await admit_cursor_dispatch(child, cfg=cfg, controller=_controller())
    assert resp.status_code == 200, bytes(resp.body).decode()
    assert pins, "pin_lane_worktree_on_admit must run"
    assert Path(str(pins[0]["source_repo"])).resolve() == satellite.resolve()
    child_row = _row("p-sat-child")
    assert child_row is not None
    assert json.loads(child_row["record_json"])["workspace"] == "cryptax"


@pytest.mark.asyncio
async def test_resume_of_explicit_workspace_mismatch_is_422_before_pin(
    tmp_path: Path, _admit_stubs: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37532 — explicit wrong workspace must 422 before lock, not 503 pin."""
    import subprocess

    from services.git_integration_worker.config import WorkerConfig
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    projects = tmp_path / "projects"
    hub = projects / "hub"
    satellite = projects / "cryptax"
    _init_git(hub)
    _init_git(satellite)
    roster = hub / "cursor-plugins/ulg-ecosystem/SATELLITES.txt"
    roster.parent.mkdir(parents=True)
    roster.write_text("cryptax\nemail-bridge\n", encoding="utf-8")
    wt = tmp_path / "lane-14724"
    tip = subprocess.run(
        ["git", "-C", str(satellite), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(satellite), "branch", "lane-14724", tip],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(satellite), "worktree", "add", str(wt), "lane-14724"],
        check=True,
        capture_output=True,
    )

    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace=projects,
        green_gate_cmd=["true"],
    )
    pins: list[dict[str, object]] = []

    def _pin(**kwargs: object) -> str:
        pins.append(kwargs)
        return "ulg:lock"

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.pin_lane_worktree_on_admit",
        _pin,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_posture."
        "b_worktree_materialized",
        lambda **_k: True,
    )

    ledger = CursorDispatchLedger.instance()
    parent = CursorDispatchRequest(
        thread_id="14724",
        model="cursor/composer-2.5",
        dispatch_id="p-sat-parent-mm",
        execution_id="exec-p-sat-parent-mm",
        caller_agent="cursor",
        message="satellite parent",
        handoff_contract="conductor",
        workspace="cryptax",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(wt),
        work_key="todo:cryptax-p5-csv-refresh",
    )
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=parent.dispatch_id,
            thread_id=parent.thread_id,
            model_id="m",
        ),
        contract="conductor",
        source_repo=str(satellite.resolve()),
        lease_key=str(wt.resolve()),
        work_key=parent.work_key,
        source_ref=parent.work_key,
        identity_class="declared",
    )
    store = tmp_path / "store-p-sat-parent-mm"
    store.mkdir(parents=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=parent.dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=parent.dispatch_id, agent_id="agent-p-sat-parent-mm", run_id="r"
    )
    ledger.mark_running(dispatch_id=parent.dispatch_id)
    mark_parked(
        dispatch_id=parent.dispatch_id,
        intent_id="intent-37532",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=1,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    child = CursorDispatchRequest(
        thread_id="14724",
        model="cursor/composer-2.5",
        dispatch_id="p-sat-child-mm",
        execution_id="exec-p-sat-child-mm",
        caller_agent="cursor",
        message="resume with wrong workspace",
        handoff_contract="conductor",
        resume_of="p-sat-parent-mm",
        workspace="email-bridge",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(wt),
        work_key="todo:cryptax-p5-csv-refresh",
    )
    resp = await admit_cursor_dispatch(child, cfg=cfg, controller=_controller())
    assert resp.status_code == 422, bytes(resp.body).decode()
    body = json.loads(bytes(resp.body).decode())
    assert body["code"] == "CURSOR_WORKSPACE_PARENT_MISMATCH"
    assert pins == []


@pytest.mark.asyncio
async def test_legacy_lane_a_park_row_strips_before_admit(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """a:37434 — parent record_json.lane A must not re-hit CURSOR_LANE_A_REFUSED."""
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    _seed_parked("p-lane-a", thread_id="37434", tmp_path=tmp_path)
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id="p-lane-a", patch={"lane": "A"}
    )
    row = load_park_row(dispatch_id="p-lane-a")
    assert row is not None and row.record.get("lane") == "A"
    cfg = load_config()
    controller = _controller()
    unstripped = build_park_resume_request(row, attempt=1, code_version="v").model_copy(
        update={"lane": "A", "dispatch_id": "p-lane-a-probe"}
    )
    blocked = await admit_cursor_dispatch(unstripped, cfg=cfg, controller=controller)
    assert blocked.status_code == 422
    body = json.loads(bytes(blocked.body).decode())
    assert body["code"] == "CURSOR_LANE_A_REFUSED"

    child = build_park_resume_request(row, attempt=1, code_version="v")
    assert child.lane is None
    summary = await resume_parked_dispatches(
        cfg=cfg, controller=controller, code_version="v", bus=_bus()
    )
    assert summary.admitted == [("p-lane-a", "p-lane-a-r1")]
    assert summary.refused == []
    assert _row("p-lane-a-r1") is not None


@pytest.mark.asyncio
async def test_park_eligible_resumes_execution_id_ineligible_not_parked(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """Eligible busy row parks and the resume child keeps the parent execution_id.

    A row with no sdk_agent_id, or with no SDK store dir, is not cancelled.
    """
    reset_park_marks()
    _seed_parked("elig", thread_id="13116", tmp_path=tmp_path, finalize=False)
    elig_run = FakeCancellableRun(id="run-elig", agent_id="agent-elig")
    register_live_run(
        dispatch_id="elig", thread_id="13116", source_repo="/repo", run=elig_run
    )
    try:
        parked = signal_park(
            "elig",
            intent_id="intent-r",
            drain_epoch=5,
            actor="manage",
            reason="deploy",
        )
        assert parked.requested and elig_run.cancel_calls == 1
        mark_parked(
            dispatch_id="elig",
            intent_id="intent-r",
            drain_epoch=5,
            actor="manage",
            reason="deploy",
            requested_at="x",
            method="run_cancel",
            tool_call_count=1,
            last_tool_calls=[],
            sidecar_uri=None,
        )
        assert _row("elig")["park_kind"] == "park_for_restart"
        parked_at = datetime.now(UTC) - timedelta(seconds=30)
        with CursorDispatchLedger.instance()._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET parked_at=? WHERE dispatch_id='elig'",
                (parked_at.isoformat(),),
            )
        summary = await resume_parked_dispatches(
            cfg=load_config(),
            controller=_controller(),
            code_version="abc1234",
            bus=_bus(),
        )
        assert summary.admitted == [("elig", "elig-r1")]
        child = _row("elig-r1")
        assert child is not None
        assert child["execution_id"] == "exec-elig"
        assert child["resume_of"] == "elig"

        _seed_parked(
            "young",
            thread_id="13116-y",
            tmp_path=tmp_path,
            finalize=False,
            omit_sdk_agent_id=True,
            lease_key=str(tmp_path / "lease-young"),
        )
        young_run = FakeCancellableRun(id="run-young")
        register_live_run(
            dispatch_id="young",
            thread_id="13116-y",
            source_repo="/repo",
            run=young_run,
        )
        refused = signal_park(
            "young", intent_id="intent-r", drain_epoch=5, actor="manage", reason="r"
        )
        assert refused.refusal is ParkRefusal.NOT_RESUMABLE_YET
        assert young_run.cancel_calls == 0
        assert _row("young")["status"] == "running"
        assert _row("young")["park_kind"] is None

        _seed_parked(
            "nostore",
            thread_id="13116-n",
            tmp_path=tmp_path,
            finalize=False,
            with_store=False,
            lease_key=str(tmp_path / "lease-nostore"),
        )
        nostore_run = FakeCancellableRun(id="run-nostore")
        register_live_run(
            dispatch_id="nostore",
            thread_id="13116-n",
            source_repo="/repo",
            run=nostore_run,
        )
        missing = signal_park(
            "nostore", intent_id="intent-r", drain_epoch=5, actor="manage", reason="r"
        )
        assert missing.refusal is ParkRefusal.STATE_ROOT_MISSING
        assert nostore_run.cancel_calls == 0
        assert _row("nostore")["status"] == "running"
        assert _row("nostore")["park_kind"] is None
    finally:
        for did in ("elig", "young", "nostore"):
            unregister_live_run(dispatch_id=did)
        reset_park_marks()
