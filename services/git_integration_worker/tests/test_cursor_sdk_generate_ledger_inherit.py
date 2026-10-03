"""Outstanding CDP generate ids inherit across resume_of (friction:37401)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.mcp_bridge_generate_ledger import (
    GenerateObserver,
    read_generate_records,
    read_received_execution_ids,
)
from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_await_reply import (
    ADMITTED_VIA_AWAIT_RESUME,
    AWAIT_REPLY_FLAG_ENV,
    PARK_KIND_AWAIT_REPLY,
    mark_await_parked,
    maybe_await_park_at_terminal,
    outstanding_generates,
)
from services.git_integration_worker.cursor_sdk_generate_ledger_inherit import (
    inherit_outstanding_generates,
)
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.cursor_sdk_park_resume import (
    ADMITTED_VIA_PARK_RESUME,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

_EXEC = "3f6a5a66-e9d3-4c55-886b-000000000001"
_EXEC2 = "e3c9f4d6-aaaa-4c55-886b-000000000002"
_THREAD = "14692"


def _spool(tmp_path: Path) -> Path:
    spool = tmp_path / "steer-spool"
    spool.mkdir(parents=True, exist_ok=True)
    return spool


def _fire(
    tmp_path: Path,
    dispatch_id: str,
    *,
    execution_id: str = _EXEC,
    after_turn: int = 3,
) -> None:
    row = {
        "execution_id": execution_id,
        "thread_id": _THREAD,
        "after_turn": after_turn,
        "from_agent": "web-anthropic",
        "model": "cdp/opus-5.5",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _received(
    tmp_path: Path, dispatch_id: str, *, execution_id: str = _EXEC
) -> None:
    row = {
        "kind": "received",
        "execution_id": execution_id,
        "tool": "wait",
        "received_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _seed_running(dispatch_id: str, *, tmp_path: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="7040",
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message=f"packet {dispatch_id}",
        handoff_contract="freeform",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / f"wt-{dispatch_id}"),
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="m",
        ),
        contract="freeform",
        source_repo=str(load_config().source_repo),
        lease_key=f"lease-{dispatch_id}",
    )
    ledger.mark_running(dispatch_id=dispatch_id)


def test_copies_outstanding_not_already_received(tmp_path: Path) -> None:
    """Parent fired two; one received — child ledger gets only the outstanding id."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p", execution_id=_EXEC)
    _received(tmp_path, "parent-p", execution_id=_EXEC)
    _fire(tmp_path, "parent-p", execution_id=_EXEC2)
    copied = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    assert copied == [_EXEC2]
    assert [
        r["execution_id"]
        for r in read_generate_records("parent-p-r1", spool_dir=spool)
    ] == [_EXEC2]
    assert outstanding_generates("parent-p-r1", spool_dir=spool)[0].execution_id == (
        _EXEC2
    )


def test_idempotent_second_call_does_not_duplicate(tmp_path: Path) -> None:
    """Replay after crash-between-insert-and-copy must not append twice."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    first = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    second = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    assert first == [_EXEC]
    assert second == []
    assert [
        r["execution_id"]
        for r in read_generate_records("parent-p-r1", spool_dir=spool)
    ] == [_EXEC]


def test_skips_await_reply_resume_to_avoid_repark_loop(tmp_path: Path) -> None:
    """Await-resume preamble already carries the reply; copying would re-park."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    copied = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-c1",
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        spool_dir=spool,
    )
    assert copied == []
    assert not (_spool(tmp_path) / "parent-p-c1.cdp-generates.jsonl").exists()


def test_missing_parent_spool_is_empty_not_an_error(tmp_path: Path) -> None:
    """Partial failure: parent JSONL gone (or never written) — fail open."""
    copied = inherit_outstanding_generates(
        parent_id="missing-parent",
        child_id="missing-parent-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=_spool(tmp_path),
    )
    assert copied == []


def test_child_observer_can_mark_inherited_generate_received(
    tmp_path: Path,
) -> None:
    """Receive path: child's empty known-set is the drop; inherit fills it."""
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    observer = GenerateObserver(dispatch_id="parent-p-r1", spool_dir=spool)
    observer.on_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "agent_bus",
                "arguments": {
                    "tool": "wait",
                    "arguments": {
                        "thread": _THREAD,
                        "after_turn": 3,
                        "execution_id": _EXEC,
                        "from_agent": "web-anthropic",
                    },
                },
            },
        }
    )
    observer.on_result(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "complete": True,
                                "qualifying_reply_turn": 4,
                                "producer": {
                                    "execution_id": _EXEC,
                                    "state": "terminal",
                                },
                            }
                        ),
                    }
                ],
                "isError": False,
            },
        }
    )
    assert read_received_execution_ids("parent-p-r1", spool_dir=spool) == {_EXEC}
    assert outstanding_generates("parent-p-r1", spool_dir=spool) == []


@pytest.mark.asyncio
async def test_child_closeout_parks_when_inherited_generate_still_outstanding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abandon path: child never waits — closeout must still await-park."""
    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "1")
    spool = _spool(tmp_path)
    _fire(tmp_path, "parent-p")
    inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=spool,
    )
    _seed_running("parent-p-r1", tmp_path=tmp_path)
    parked = await maybe_await_park_at_terminal(
        dispatch_id="parent-p-r1",
        thread_id="7040",
        execution_id="exec-parent-p-r1",
        bus=MagicMock(),
        spool_dir=spool,
    )
    assert parked is True
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT park_kind FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-p-r1",),
        ).fetchone()
    assert row["park_kind"] == PARK_KIND_AWAIT_REPLY


_ROUTE_THREAD = "7040"


@pytest.fixture
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
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.pin_lane_worktree_on_admit",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_posture."
        "b_worktree_materialized",
        lambda **_k: True,
    )
    return spawned


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at=datetime.now(UTC).isoformat(),
    )


def _route_spool() -> Path:
    from services.git_integration_worker.cursor_sdk_context import steer_spool_dir

    root = steer_spool_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fire_route(
    dispatch_id: str, *, execution_id: str = _EXEC, after_turn: int = 3
) -> None:
    row = {
        "execution_id": execution_id,
        "thread_id": _THREAD,
        "after_turn": after_turn,
        "from_agent": "web-anthropic",
        "model": "cdp/opus-5.5",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    path = _route_spool() / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _child_jsonl(dispatch_id: str) -> Path:
    return _route_spool() / f"{dispatch_id}.cdp-generates.jsonl"


def _seed_resume_parent(
    dispatch_id: str, *, tmp_path: Path, work_key: str, thread_id: str = _ROUTE_THREAD
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message=f"packet {dispatch_id}",
        handoff_contract="freeform",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / f"wt-{dispatch_id}"),
        work_key=work_key,
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="m",
        ),
        contract="freeform",
        source_repo=str(load_config().source_repo),
        lease_key=f"lease-{dispatch_id}",
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared",
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )


def _park_for_restart(dispatch_id: str) -> None:
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-37401",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=1,
        last_tool_calls=[],
        sidecar_uri=None,
        park_kind="park_for_restart",
    )


def _resume_req(
    *,
    parent_id: str,
    child_id: str,
    tmp_path: Path,
    admitted_via: str | None,
    work_key: str,
    thread_id: str = _ROUTE_THREAD,
) -> CursorDispatchRequest:
    wt = tmp_path / f"wt-{child_id}"
    wt.mkdir(parents=True, exist_ok=True)
    return CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=child_id,
        execution_id=f"exec-{child_id}",
        caller_agent="cursor",
        message=f"resume {child_id}",
        handoff_contract="freeform",
        resume_of=parent_id,
        admitted_via=admitted_via,
        lane="B",
        worktree_isolated=True,
        worktree_path=str(wt),
        work_key=work_key,
    )


@pytest.mark.asyncio
async def test_admit_hook_copies_replay_skips_refused_and_await_resume(
    tmp_path: Path, _admit_stubs: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong hook placement still passes unit inherit tests; this pins the route."""
    monkeypatch.setenv("ULG_STEER_SPOOL_DIR", str(_route_spool()))
    work_key = "todo:friction-37401-hook"
    _seed_resume_parent("parent-p", tmp_path=tmp_path, work_key=work_key)
    _park_for_restart("parent-p")
    _fire_route("parent-p")
    child = _resume_req(
        parent_id="parent-p",
        child_id="parent-p-r1",
        tmp_path=tmp_path,
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        work_key=work_key,
    )
    first = await admit_cursor_dispatch(
        child, cfg=load_config(), controller=_controller()
    )
    assert first.status_code in (200, 202), bytes(first.body).decode()
    records = read_generate_records("parent-p-r1", spool_dir=_route_spool())
    assert [r["execution_id"] for r in records] == [_EXEC]

    replay = await admit_cursor_dispatch(
        child, cfg=load_config(), controller=_controller()
    )
    assert replay.status_code in (200, 202), bytes(replay.body).decode()
    assert [
        r["execution_id"]
        for r in read_generate_records("parent-p-r1", spool_dir=_route_spool())
    ] == [_EXEC]

    await_key = "todo:friction-37401-await"
    await_thread = "7041"
    _seed_resume_parent(
        "parent-await", tmp_path=tmp_path, work_key=await_key, thread_id=await_thread
    )
    _fire_route("parent-await")
    awaited = outstanding_generates("parent-await", spool_dir=_route_spool())
    assert mark_await_parked(dispatch_id="parent-await", awaited=awaited) is not None
    CursorDispatchLedger.instance().mark_terminal(
        dispatch_id="parent-await", terminal_status="completed"
    )
    taken = _resume_req(
        parent_id="parent-await",
        child_id="parent-await-c1",
        tmp_path=tmp_path,
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        work_key=await_key,
        thread_id=await_thread,
    )
    taken_resp = await admit_cursor_dispatch(
        taken, cfg=load_config(), controller=_controller()
    )
    assert taken_resp.status_code in (200, 202), bytes(taken_resp.body).decode()
    refused = _resume_req(
        parent_id="parent-await",
        child_id="parent-await-c2",
        tmp_path=tmp_path,
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        work_key=await_key,
        thread_id=await_thread,
    )
    blocked = await admit_cursor_dispatch(
        refused, cfg=load_config(), controller=_controller()
    )
    assert blocked.status_code == 409, bytes(blocked.body).decode()
    body = json.loads(bytes(blocked.body).decode())
    assert body.get("code") == "CURSOR_RESUME_ALREADY_ADMITTED"
    assert not _child_jsonl("parent-await-c2").exists()

    skip_key = "todo:friction-37401-skip"
    skip_thread = "7042"
    _seed_resume_parent(
        "parent-skip", tmp_path=tmp_path, work_key=skip_key, thread_id=skip_thread
    )
    _park_for_restart("parent-skip")
    _fire_route("parent-skip")
    skip = _resume_req(
        parent_id="parent-skip",
        child_id="parent-skip-c1",
        tmp_path=tmp_path,
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        work_key=skip_key,
        thread_id=skip_thread,
    )
    skip_resp = await admit_cursor_dispatch(
        skip, cfg=load_config(), controller=_controller()
    )
    assert skip_resp.status_code in (200, 202), bytes(skip_resp.body).decode()
    assert not _child_jsonl("parent-skip-c1").exists()


def test_inherit_fail_open_when_outstanding_generates_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing-file returns early; this covers the except Exception branch."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_generate_ledger_inherit."
        "outstanding_generates",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("spool unreadable")),
    )
    copied = inherit_outstanding_generates(
        parent_id="parent-p",
        child_id="parent-p-r1",
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        spool_dir=_spool(tmp_path),
    )
    assert copied == []


@pytest.mark.asyncio
async def test_admit_fail_open_when_outstanding_generates_raises(
    tmp_path: Path, _admit_stubs: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULG_STEER_SPOOL_DIR", str(_route_spool()))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_generate_ledger_inherit."
        "outstanding_generates",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("spool unreadable")),
    )
    work_key = "todo:friction-37401-fail-open"
    _seed_resume_parent("parent-err", tmp_path=tmp_path, work_key=work_key)
    _park_for_restart("parent-err")
    _fire_route("parent-err")
    child = _resume_req(
        parent_id="parent-err",
        child_id="parent-err-r1",
        tmp_path=tmp_path,
        admitted_via=ADMITTED_VIA_PARK_RESUME,
        work_key=work_key,
    )
    resp = await admit_cursor_dispatch(
        child, cfg=load_config(), controller=_controller()
    )
    assert resp.status_code in (200, 202), bytes(resp.body).decode()
    assert not _child_jsonl("parent-err-r1").exists()
