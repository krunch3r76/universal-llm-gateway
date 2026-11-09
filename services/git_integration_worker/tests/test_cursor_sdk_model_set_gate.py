"""GIW model-set gate and re-admit refusals (AC 8–9', T7)."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.cursor_sdk_park_resume import resume_parked_dispatches
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.app import create_app
from services.git_integration_worker.tests.test_cursor_sdk_route import _dispatch_body

# Not in CURSOR_MODEL_CAPABILITIES — park/nest/await refusal tests stay valid.
_UNTRUSTED_CURSOR_MODEL = "cursor/claude-sonnet-5-ultra-fake"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _route_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker.cursor_sdk_context import (
        CursorApiKeyResolution,
    )

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
    monkeypatch.setattr(
        WorkAdmissionController,
        "create_tracked_task",
        MagicMock(return_value=MagicMock(done=lambda: False)),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.pin_lane_worktree_on_admit",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_posture."
        "b_worktree_materialized",
        lambda **_k: True,
    )


def test_admit_cursor_dispatch_has_no_enforce_model_set_kwarg() -> None:
    sig = inspect.signature(route_mod.admit_cursor_dispatch)
    assert "enforce_model_set" not in sig.parameters


def test_enforce_model_set_absent_in_production_giw() -> None:
    root = Path(__file__).resolve().parents[1]
    hits = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "enforce_model_set" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []


def test_http_explicit_other_models_refused_pool_fence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="cursor/claude-fable-5"),
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload.get("code") == "other_models_pool_denied"
    bridge.assert_not_called()


def test_http_admits_composer_primary_pool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="composer-2.5"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("code") != "CURSOR_MODEL_UNTRUSTED"


def test_http_bare_other_models_pool_id_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="claude-fable-5"),
    )
    assert resp.status_code == 422
    assert resp.json().get("code") == "other_models_pool_denied"
    bridge.assert_not_called()


def test_http_refuses_untrusted_bare_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="claude-sonnet-5-ultra-fake"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_MODEL_UNTRUSTED"
    bridge.assert_not_called()


def _bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={"turn_number": 1}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _seed_parked_non_member(
    dispatch_id: str,
    *,
    tmp_path: Path,
    thread_id: str = "22144",
    stored_model: str = _UNTRUSTED_CURSOR_MODEL,
    resolved_model: str = "claude-sonnet-5-ultra-fake",
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model=stored_model,
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message="parked non-member",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model=resolved_model,
        admission=CursorDispatchResponse(
            admitted=True, dispatch_id=dispatch_id, thread_id=thread_id, model_id="m"
        ),
        contract="none",
        source_repo=str(load_config().source_repo),
        lease_key=str(load_config().source_repo),
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"model": stored_model},
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id,
        agent_id=f"agent-{dispatch_id}",
        run_id="r",
    )
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x", encoding="utf-8")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-ms",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    parked_at = datetime.now(UTC) - timedelta(hours=2)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET parked_at=?, park_expires_at=? WHERE dispatch_id=?",
            (
                parked_at.isoformat(),
                (parked_at + timedelta(days=1)).isoformat(),
                dispatch_id,
            ),
        )


@pytest.mark.asyncio
async def test_t7_1_park_resume_refuses_stored_other_models_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Park re-admit refuses stored Other Models pool rows when policy is deny."""
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    _seed_parked_non_member(
        "p-other",
        tmp_path=tmp_path,
        stored_model="cursor/claude-fable-5",
        resolved_model="claude-fable-5",
    )
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await resume_parked_dispatches(
        cfg=load_config(),
        controller=WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="w",
            pid=0,
            worker_started_at=started,
        ),
        code_version="gate-test",
        bus=_bus(),
    )
    assert ("p-other", "other_models_pool_denied") in summary.refused
    bridge.assert_not_called()


@pytest.mark.asyncio
async def test_t7_1_park_resume_refuses_stored_non_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7-1 / T7-8: park re-admit refuses untrusted model; no child row."""
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    _seed_parked_non_member("p-fake", tmp_path=tmp_path)
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await resume_parked_dispatches(
        cfg=load_config(),
        controller=WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="w",
            pid=0,
            worker_started_at=started,
        ),
        code_version="gate-test",
        bus=_bus(),
    )
    assert ("p-fake", "CURSOR_MODEL_UNTRUSTED") in summary.refused
    child_id = "p-fake-r1"
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (child_id,),
        ).fetchone()
    assert row is None
    bridge.assert_not_called()


def test_member_bare_composer_not_model_set_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after model_set is acceptable; must not be model_set."""
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="composer-2.5", dispatch_id="disp-member-bare"),
    )
    if resp.status_code == 422:
        data = resp.json().get("data") or {}
        assert data.get("validation_stage") != "model_set"


def _seed_member_row(
    dispatch_id: str,
    *,
    tmp_path: Path,
    stored_model: str,
    resolved_model: str,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="22144",
        model=stored_model,
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message="member seed",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / f"wt-{dispatch_id}"),
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model=resolved_model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id=resolved_model.split("/")[-1],
        ),
        contract="none",
        source_repo=str(load_config().source_repo),
        lease_key=str(load_config().source_repo),
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"model": stored_model},
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x", encoding="utf-8")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))


@pytest.mark.parametrize(
    "stored_model,resolved_model",
    [
        ("composer-2.5", "composer-2.5"),
        ("grok-4.7", "grok-4.7"),
        ("cursor/composer-2.5", "composer-2.5"),
        ("cursor/COMPOSER-2.5", "composer-2.5"),
    ],
)
@pytest.mark.asyncio
async def test_t7_4_member_forms_not_model_set_refused_on_readmit(
    tmp_path: Path,
    stored_model: str,
    resolved_model: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7-4 (F2): park, nest, and await builders keep admitted member spellings."""
    from services.git_integration_worker.cursor_sdk_await_reply import (
        build_await_resume_request,
    )
    from services.git_integration_worker.cursor_sdk_nest_resume import _resume_request
    from services.git_integration_worker.cursor_sdk_park_ledger import load_park_row
    from services.git_integration_worker.cursor_sdk_park_resume import (
        build_park_resume_request,
    )
    parent = f"mem-{stored_model.replace('/', '-')}"
    _seed_member_row(
        parent,
        tmp_path=tmp_path,
        stored_model=stored_model,
        resolved_model=resolved_model,
    )
    mark_parked(
        dispatch_id=parent,
        intent_id="i",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    row = load_park_row(dispatch_id=parent)
    assert row is not None
    park_req = build_park_resume_request(row, attempt=1, code_version="v")
    nest_req = _resume_request(parent, child_id=f"{parent}-child")
    assert nest_req is not None
    await_req = build_await_resume_request(
        row,
        replies=[],
        attempt=1,
        code_version="v",
        ttl_s=3600,
        bus_unreachable=False,
        hub=load_config().source_repo,
    )
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at=datetime.now(UTC).isoformat(),
    )
    cfg = load_config()
    for req in (park_req, nest_req, await_req):
        resp = await route_mod.admit_cursor_dispatch(
            req, cfg=cfg, controller=controller
        )
        if resp.status_code == 422:
            body = json.loads(bytes(resp.body).decode())
            if body.get("code") == "CURSOR_MODEL_UNTRUSTED":
                data = body.get("data") or {}
                assert data.get("validation_stage") != "model_set", (
                    stored_model,
                    req.dispatch_id,
                )


@pytest.mark.asyncio
async def test_t7_7_conductor_park_refusal_does_not_stamp_hop_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7-7 (F3): conductor park resume refused at model_set leaves hop successor unset."""
    from services.git_integration_worker.cursor_sdk_park_resume import (
        resume_parked_dispatches as park_resume,
    )

    stamp = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_park_resume._stamp_hop_successor",
        stamp,
    )
    dispatch_id = "p-cond-fake"
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="22144",
        model=_UNTRUSTED_CURSOR_MODEL,
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message="conductor argon",
        handoff_contract="conductor",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="claude-sonnet-5-ultra-fake",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="claude-sonnet-5-ultra-fake",
        ),
        contract="conductor",
        source_repo=str(load_config().source_repo),
        lease_key=str(load_config().source_repo),
        packet_kind="conductor",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"model": _UNTRUSTED_CURSOR_MODEL, "contract": "conductor"},
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x", encoding="utf-8")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    parked_at = datetime.now(UTC) - timedelta(hours=2)
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-ms",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET parked_at=?, park_expires_at=? WHERE dispatch_id=?",
            (
                parked_at.isoformat(),
                (parked_at + timedelta(days=1)).isoformat(),
                dispatch_id,
            ),
        )
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await park_resume(
        cfg=load_config(),
        controller=WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="w",
            pid=0,
            worker_started_at=started,
        ),
        code_version="gate-test",
        bus=_bus(),
    )
    assert (dispatch_id, "CURSOR_MODEL_UNTRUSTED") in summary.refused
    stamp.assert_not_called()


@pytest.mark.asyncio
async def test_t7_6_park_refusal_repeats_then_single_park_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7-6: refused park row stays open across ticks; one PARK EXPIRED turn."""
    _seed_parked_non_member("p-ttl", tmp_path=tmp_path)
    started = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at=started,
    )
    bus = _bus()
    first = await resume_parked_dispatches(
        cfg=load_config(),
        controller=controller,
        code_version="gate-test",
        bus=bus,
    )
    assert ("p-ttl", "CURSOR_MODEL_UNTRUSTED") in first.refused
    assert first.expired == []
    second = await resume_parked_dispatches(
        cfg=load_config(),
        controller=controller,
        code_version="gate-test",
        bus=bus,
    )
    assert ("p-ttl", "CURSOR_MODEL_UNTRUSTED") in second.refused
    parked_at = datetime.now(UTC) - timedelta(hours=2)
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_expires_at=? WHERE dispatch_id=?",
            ((parked_at + timedelta(seconds=1)).isoformat(), "p-ttl"),
        )
    third = await resume_parked_dispatches(
        cfg=load_config(),
        controller=controller,
        code_version="gate-test",
        bus=bus,
    )
    assert third.expired == ["p-ttl"]
    expired_subjects = [
        c.kwargs.get("subject", "")
        for c in bus.reply.await_args_list
        if "PARK EXPIRED" in str(c.kwargs.get("subject", ""))
    ]
    assert len(expired_subjects) == 1
    fourth = await resume_parked_dispatches(
        cfg=load_config(),
        controller=controller,
        code_version="gate-test",
        bus=bus,
    )
    assert fourth.expired == []
    assert (
        len(
            [
                c
                for c in bus.reply.await_args_list
                if "PARK EXPIRED" in str(c.kwargs.get("subject", ""))
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_t7_3_nest_refuses_non_member_and_releases_handed_slot_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7-3: nest re-admit 422 releases the handed slot once."""
    from universal_concurrency import FifoCapacityGate

    from services.git_integration_worker.cursor_sdk_gate import (
        acquire_sdk_dispatch_slot,
        sdk_dispatch_gate_holders,
    )
    from services.git_integration_worker.cursor_sdk_nest_resume import (
        admit_nest_parent_resume,
        claim_nest_parent_exit,
    )

    isolated = FifoCapacityGate(limit=1, gate_id="t7-3")
    gates = {"standard": isolated, "operator": isolated}
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._STANDARD_GATE", isolated
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._OPERATOR_GATE", isolated
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._LANE_GATES", gates
    )
    release = MagicMock(wraps=__import__(
        "services.git_integration_worker.cursor_sdk_nest_resume",
        fromlist=["x"],
    )._release_handed_slot)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_nest_resume._release_handed_slot",
        release,
    )
    undo = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_nest_resume."
        "undo_unadmitted_hand_claim",
        undo,
    )

    from services.git_integration_worker.tests.test_cursor_sdk_nest_resume import (
        _admit as nest_seed_admit,
    )

    parent_id = "nest-argon"
    child_id = "nest-argon-child"
    ledger = CursorDispatchLedger.instance()
    nest_seed_admit(ledger, parent_id, contract="freeform")
    nest_seed_admit(
        ledger,
        child_id,
        contract="freeform",
        nest_under=parent_id,
        work_key=f"friction:{parent_id}",
        lease_key=f"/repo/{parent_id}",
    )
    for did in (parent_id, child_id):
        ledger.merge_record_json(
            dispatch_id=did,
            patch={"model": _UNTRUSTED_CURSOR_MODEL},
        )
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET resolved_model=? WHERE dispatch_id=?",
                ("claude-sonnet-5-ultra-fake", did),
            )
    assert claim_nest_parent_exit(parent_id) == child_id
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="completed")
    await acquire_sdk_dispatch_slot(dispatch_id=parent_id)
    result = await admit_nest_parent_resume(
        parent_id,
        child_id=child_id,
        controller=WorkAdmissionController(
            ledger=ledger, worker_id="w", pid=0, worker_started_at=datetime.now(UTC).isoformat()
        ),
        cfg=load_config(),
    )
    assert result.outcome == "refused"
    assert f"{parent_id}-nr1" not in sdk_dispatch_gate_holders()
    release.assert_called_once()
    undo.assert_not_called()
    with ledger._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (f"{parent_id}-nr1",),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.asyncio
async def test_t7_2_await_refusal_cap_expires_non_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7-2: await re-admit refuses non-member until cap expires the row."""
    from services.git_integration_worker import cursor_sdk_await_reply as await_mod
    from services.git_integration_worker.cursor_sdk_await_reply import (
        AWAIT_REPLY_FLAG_ENV,
        resume_await_parked_dispatches,
    )
    from services.git_integration_worker.tests import test_cursor_sdk_await_reply as await_h

    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "1")
    monkeypatch.setattr(await_mod, "_MAX_RESUME_ATTEMPTS", 2)
    dispatch_id = "d-await-fake"
    _seed_member_row(
        dispatch_id,
        tmp_path=tmp_path,
        stored_model=_UNTRUSTED_CURSOR_MODEL,
        resolved_model="claude-sonnet-5-ultra-fake",
    )
    await_h._record_generate(tmp_path, dispatch_id)
    bus = await_h._bus()
    turns = await_h._Turns({await_h._COORD_THREAD: [await_h._reply_turn()]})
    await await_h._park(dispatch_id, bus=bus, turns=turns, tmp_path=tmp_path)
    await_h._mark_completed(dispatch_id)
    first = await resume_await_parked_dispatches(
        cfg=load_config(),
        controller=await_h._controller(),
        code_version="abc",
        bus=bus,
        bus_turns_fn=turns,
    )
    assert first.refused == [(dispatch_id, "CURSOR_MODEL_UNTRUSTED")]
    assert first.expired == []
    second = await resume_await_parked_dispatches(
        cfg=load_config(),
        controller=await_h._controller(),
        code_version="abc",
        bus=bus,
        bus_turns_fn=turns,
    )
    assert second.refused == [(dispatch_id, "CURSOR_MODEL_UNTRUSTED")]
    assert second.expired == [dispatch_id]
    assert json.loads(await_h._row(dispatch_id)["record_json"])["park"].get("expired_at")
