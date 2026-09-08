"""Parked terminal through the gated coroutine (AC-SR-1, AC-SR-10, AC-SR-11).

Drives ``_run_sdk_dispatch_gated`` with a stubbed worker whose run was park-
cancelled (the in-process park mark is set before the coroutine observes the
worker result), and asserts the D6 finalize contract: terminal ``cancelled`` +
park columns, PARKED bus turn, no link termination, conductor rows graded as a
designed stop, and no Lane-B disposition.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles.conductor_stop import parse_stop_tokens

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import hop_owed
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    park_harvest_owed,
)
from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
    build_parked_body,
    parked_wake_line,
)
from services.git_integration_worker.cursor_sdk_park import (
    conductor_hop_watchdog_candidates,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkMark,
    _marks,
    park_mark,
    reset_park_marks,
)
from services.git_integration_worker.cursor_sdk_resume import resume_eligibility_reason
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.tests.conftest import _ctx

_INTENT = "intent-fin-1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    reset_park_marks()
    from services.git_integration_worker.cursor_sdk_events import (
        reset_terminal_emitted_registry,
    )

    reset_terminal_emitted_registry()
    yield
    reset_park_marks()
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _route_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {"files": {}}
    )

    async def _noop_acquire(**_kw: Any) -> str:
        return _kw.get("dispatch_id") or "slot"

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child_sync", lambda *_a, **_k: "released"
    )
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
        route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None
    )


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []
    for mod in (
        "services.git_integration_worker.cursor_sdk_park_events",
        "services.git_integration_worker.cursor_sdk_cancel_events",
    ):
        monkeypatch.setattr(f"{mod}.emit_frontier_event", lambda ev: log.append(ev))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit",
        lambda ev: log.append(ev),
    )
    return log


def _mock_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 7})
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


def _admit(
    req: CursorDispatchRequest, *, tmp_path: Path, contract: str | None = None
) -> None:
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract=contract,
        source_repo=str(tmp_path / "repo"),
        lease_key=str(tmp_path / "repo"),
        work_key="todo:steer-fin",
        identity_class="declared",
        packet_kind="conductor" if contract == "conductor" else None,
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)
    store = tmp_path / f"store-{req.dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=req.dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=req.dispatch_id, agent_id="agent-fin", run_id="r"
    )
    if contract == "conductor":
        ledger.merge_record_json(
            dispatch_id=req.dispatch_id,
            patch={"contract": "conductor", "packet_kind": "conductor"},
        )


def _set_mark(
    dispatch_id: str, thread_id: str, *, method: str = "run_cancel"
) -> ParkMark:
    mark = ParkMark(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        intent_id=_INTENT,
        drain_epoch=2,
        actor="manage",
        reason="deploy",
        requested_at="2026-09-08T05:00:00+00:00",
        method=method,
        marked_at=time.monotonic(),
    )
    _marks[dispatch_id] = mark
    return mark


def _row(dispatch_id: str) -> dict[str, Any]:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def _cancelled_outcome(tool_calls: int = 5) -> SdkRunOutcome:
    calls = tuple(
        ToolCallObservation(
            call_id=f"c{i}",
            tool_name="fs" if i % 2 else "shell",
            status="completed",
            arg_bytes=1,
            result_bytes=1,
            truncated_fields=(),
        )
        for i in range(tool_calls)
    )
    return SdkRunOutcome(
        body="",
        status="cancelled",
        duration_ms=1234,
        tool_call_count=tool_calls,
        tool_calls=calls,
        sdk_request_id="req",
        request_id_source="stream",
    )


@pytest.mark.asyncio
async def test_gated_park_branch_finalizes_parked_without_link_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    disposition_calls: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_b_disposition."
        "mark_lane_b_disposition_for_dispatch",
        lambda **kw: disposition_calls.append(kw["dispatch_id"]),
    )
    req = CursorDispatchRequest(
        thread_id="9001",
        model="cursor/composer-2.5",
        dispatch_id="fin-1",
        execution_id="exec-fin-1",
        message="do work",
        handoff_contract="none",
    )
    _admit(req, tmp_path=tmp_path)
    _set_mark("fin-1", "9001")
    monkeypatch.setattr(route_mod, "_run_sdk_sync", lambda **_kw: _cancelled_outcome(5))
    bus = _mock_bus()
    controller = _controller()
    controller.try_admit("cursor_sdk", op_id="fin-1", route="/api/v1/cursor/dispatch")

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        ctx=_ctx(
            tmp_path / "repo", dispatch_id="fin-1", thread_id="9001", contract="none"
        ),
        bus=bus,
        controller=controller,
    )

    row = _row("fin-1")
    assert row["status"] == "cancelled" and row["terminal_status"] == "cancelled"
    assert row["park_kind"] == "park_for_restart" and row["park_intent_id"] == _INTENT
    record = json.loads(row["record_json"])
    assert record["park"]["method"] == "run_cancel"
    assert record["park"]["tool_call_count"] == 5
    assert record["park"]["last_tool_calls"][-1]["tool_name"] in {"fs", "shell"}
    assert record["park"]["sidecar_uri"].endswith("9001-park-partial-fin-1.md")
    assert record["resume_retain"] is True
    sidecar = (
        tmp_path / "cortex" / record["park"]["sidecar_uri"].removeprefix("cortex://")
    )
    payload = json.loads(sidecar.read_text())
    assert (
        payload["status"] == "parked"
        and payload["degraded_reason"] == "park_for_restart"
    )

    bus.terminate_dispatch.assert_not_awaited()  # AC-SR-11
    reply = bus.reply.await_args.kwargs
    assert (
        reply["subject"]
        == f"cursor-sdk dispatch fin-1 PARKED (for GIW restart {_INTENT})"
    )
    assert '"status": "parked"' in reply["body"]
    assert "PARKED_TRANSPORT" not in reply["body"]  # not a conductor row
    assert disposition_calls == []
    assert park_mark("fin-1") is None
    signals = [ev.signal for ev in events]
    assert "sdk.park.parked" in signals
    assert "frontier.sdk.worker.failed" not in signals  # I-SR-3
    assert (
        resume_eligibility_reason(CursorDispatchLedger.instance(), parent_id="fin-1")
        is None
    )


@pytest.mark.asyncio
async def test_gated_park_branch_survives_aborted_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="9002",
        model="cursor/composer-2.5",
        dispatch_id="fin-2",
        execution_id="exec-fin-2",
        message="do work",
    )
    _admit(req, tmp_path=tmp_path)
    _set_mark("fin-2", "9002", method="bridge_abort")

    def _boom(**_kw: Any) -> SdkRunOutcome:
        raise route_mod.SdkRunAbortedError(
            "stream closed",
            forensics={
                "stream_tool_call_count": 3,
                "last_tool_calls": [{"tool_name": "grep", "status": "completed"}],
            },
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _boom)
    bus = _mock_bus()
    await route_mod._run_sdk_dispatch_gated(
        req=req,
        ctx=_ctx(tmp_path / "repo", dispatch_id="fin-2", thread_id="9002"),
        bus=bus,
        controller=_controller(),
    )
    row = _row("fin-2")
    assert row["status"] == "cancelled" and row["park_kind"] == "park_for_restart"
    record = json.loads(row["record_json"])
    assert record["park"]["tool_call_count"] == 3
    assert record["park"]["method"] == "bridge_abort"
    bus.terminate_dispatch.assert_not_awaited()
    assert "frontier.sdk.worker.failed" not in [ev.signal for ev in events]


@pytest.mark.asyncio
async def test_conductor_park_is_designed_stop_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="9003",
        model="cursor/composer-2.5",
        dispatch_id="fin-cond",
        execution_id="exec-fin-cond",
        message="---\ncontract: conductor\n---\nUse the conductor skill",
        handoff_contract="conductor",
    )
    _admit(req, tmp_path=tmp_path, contract="conductor")
    _set_mark("fin-cond", "9003")
    monkeypatch.setattr(route_mod, "_run_sdk_sync", lambda **_kw: _cancelled_outcome(9))
    hop_posts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
        "post_conductor_hop_team_dispatch",
        AsyncMock(side_effect=lambda body, **_k: hop_posts.append(body) or (True, {})),
    )
    bus = _mock_bus()
    await route_mod._run_sdk_dispatch_gated(
        req=req,
        ctx=_ctx(
            tmp_path / "repo",
            dispatch_id="fin-cond",
            thread_id="9003",
            contract="conductor",
        ),
        bus=bus,
        controller=_controller(),
    )
    body = bus.reply.await_args.kwargs["body"]
    assert parked_wake_line(_INTENT) in body
    assert "PARKED_TRANSPORT" in parse_stop_tokens(body).tokens  # AC-SR-10
    row = _row("fin-cond")
    record = json.loads(row["record_json"])
    assert "PARKED_TRANSPORT" in record["closeout_stop_tokens"]
    assert record["closeout_turn"] == 7
    assert hop_owed(row) is False
    assert (
        park_harvest_owed(row) is False
    )  # wake is the resume child, not a CDP harvest
    assert (
        hop_posts == []
    )  # reactor skipped: exit-persist token, no successor minted here
    assert "fin-cond" not in conductor_hop_watchdog_candidates(
        CursorDispatchLedger.instance(), grace_s=0.0, now=time.time() + 10_000
    )


def test_build_parked_body_shapes() -> None:
    mark = ParkMark(
        dispatch_id="d",
        thread_id="t",
        intent_id="i",
        drain_epoch=1,
        actor="a",
        reason="r",
        requested_at="x",
        method="run_cancel",
        marked_at=0.0,
    )
    plain = build_parked_body(
        dispatch_id="d",
        mark=mark,
        park={"method": "run_cancel"},
        sidecar_uri=None,
        conductor=False,
    )
    assert plain.startswith("```json") and "PARKED_TRANSPORT" not in plain
    cond = build_parked_body(
        dispatch_id="d", mark=mark, park={}, sidecar_uri="cortex://x", conductor=True
    )
    assert cond.splitlines()[0] == "stop: PARKED_TRANSPORT"
    assert cond.splitlines()[1] == "PARKED_TRANSPORT wake=giw_restart:i"
    payload = json.loads(cond.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert payload["resume_of"] == "d" and payload["wake"] == "giw_restart:i"
