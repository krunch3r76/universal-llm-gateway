"""G3: conductor closeout merge → promote → conditional terminate ordering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.turns_models import ThreadStatus
from fastapi.testclient import TestClient
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    CloseoutDelivery,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod

pytestmark = pytest.mark.offline

_ROW_HOP_BODY = """\
status: complete
stop: ROW_HOP
hop_seq: 1
"""
_ROW_PINNED_BODY = """\
status: complete
stop: ROW_PINNED
"""
_ORDINARY_BODY = """\
status: complete
"""


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    from services.git_integration_worker.cursor_sdk_events import (
        reset_terminal_emitted_registry,
    )

    reset_terminal_emitted_registry()
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture()
def bus_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w-terminate-order",
        pid=0,
        worker_started_at="2026-10-01T00:00:00Z",
    )


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "14073",
        "model": "cursor/composer-2.5",
        "dispatch_id": "cond-closeout-1",
        "execution_id": "exec-cond-closeout-1",
        "message": "---\ncontract: conductor\n---\n",
        "handoff_contract": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_running_conductor(req: CursorDispatchRequest, tmp_path: Path) -> None:
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
        contract="conductor",
        source_repo=str(tmp_path / "repo"),
        lease_key=str(tmp_path / "repo"),
        work_key="todo:conductor-closeout-completes-open-mission",
        source_ref="todo:conductor-closeout-completes-open-mission",
        hop_seq=1,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"contract": "conductor", "lane": "B"},
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)


def _delivery(body: str) -> CloseoutDelivery:
    return CloseoutDelivery(
        body=body,
        sidecar_ref="workspaces://universal-llm-gateway/tmp/s.md",
        sidecar_path=None,
        full_result_bytes=len(body),
        closeout_status=CloseoutStatus.COMPLETE,
    )


def _outcome(body: str) -> SdkRunOutcome:
    return SdkRunOutcome(
        body=body,
        status="finished",
        duration_ms=500,
        tool_call_count=2,
    )


def _seed_ephemeral_bus_thread(bus_db: TestClient, *, execution_id: str) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=f"sdk-{execution_id}",
        from_agent="dispatch",
        to_agent=f"cursor-sdk:dispatch:{execution_id}",
        subject="conductor packet",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
    )
    resp = bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch result",
            "body": "worker finished",
            "after_turn": 1,
        },
    )
    assert resp.status_code == 201
    return thread_id


def _thread_row(bus_db: TestClient, thread_id: str) -> dict[str, Any]:
    resp = bus_db.get(f"/threads/{thread_id}")
    assert resp.status_code == 200
    return resp.json()


async def _real_bus_client(bus_db: TestClient, monkeypatch: pytest.MonkeyPatch):
    from services.git_integration_worker.cursor_bus import CursorBusClient

    transport = httpx.ASGITransport(app=bus_db.app)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *_a, **_k: httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ),
    )
    return CursorBusClient(base_url="http://testserver", token="")


def _install_closeout_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    closeout_body: str,
    call_order: list[str],
    merge_raises: BaseException | None = None,
) -> None:
    async def _prep(**_kw: Any) -> CloseoutDelivery:
        return _delivery(closeout_body)

    monkeypatch.setattr(route_mod, "prepare_closeout_delivery_async", _prep)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_closeout_pager."
        "page_conductor_silence",
        AsyncMock(),
    )
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", AsyncMock())
    monkeypatch.setattr(
        CursorDispatchLedger,
        "read_wt_baseline",
        lambda self, **_kw: {"files": {}, "codes": {}},
    )
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child", AsyncMock(return_value="released")
    )
    monkeypatch.setattr(
        route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None
    )
    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", AsyncMock())

    orig_merge = route_mod.merge_conductor_closeout_hop_authority

    def _track_merge(**kw: Any) -> None:
        call_order.append("merge")
        return orig_merge(**kw)

    monkeypatch.setattr(
        route_mod, "merge_conductor_closeout_hop_authority", _track_merge
    )
    if merge_raises is not None:
        boom = merge_raises

        def _raise_inner(**_kw: Any) -> None:
            raise boom

        monkeypatch.setattr(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._merge_conductor_closeout_hop_authority",
            _raise_inner,
        )

    orig_promote = route_mod._mark_terminal_and_promote

    async def _track_promote(**kw: Any) -> None:
        call_order.append("promote")
        return await orig_promote(**kw)

    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", _track_promote)

    orig_terminate = route_mod._terminate_link

    async def _track_terminate(*a: Any, **kw: Any) -> None:
        call_order.append("terminate")
        return await orig_terminate(*a, **kw)

    monkeypatch.setattr(route_mod, "_terminate_link", _track_terminate)


@pytest.mark.asyncio
async def test_row_hop_thread_active_before_hop_merge(
    tmp_path: Path,
    bus_db: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _seed_ephemeral_bus_thread(bus_db, execution_id="exec-cond-closeout-1")
    req = _req(thread_id=thread_id)
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body=_ROW_HOP_BODY, call_order=call_order
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
        "post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(False, {"reason": "stargate_unreachable"})),
    )
    bus = await _real_bus_client(bus_db, monkeypatch)
    controller = _controller()

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome(_ROW_HOP_BODY),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=controller,
        packet_text="---\ncontract: conductor\n---\n",
    )

    thread = _thread_row(bus_db, req.thread_id)
    assert thread["status"] == ThreadStatus.ACTIVE
    assert thread.get("bus_lifecycle_state") != "completed"
    assert call_order == ["merge", "promote"]
    assert "terminate" not in call_order


@pytest.mark.asyncio
async def test_exit_persist_terminates_after_merge(
    tmp_path: Path,
    bus_db: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _seed_ephemeral_bus_thread(bus_db, execution_id="exec-pinned-1")
    req = _req(
        dispatch_id="cond-pinned-1",
        execution_id="exec-pinned-1",
        thread_id=thread_id,
    )
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body=_ROW_PINNED_BODY, call_order=call_order
    )
    bus = await _real_bus_client(bus_db, monkeypatch)

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome(_ROW_PINNED_BODY),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=_controller(),
        packet_text="---\ncontract: conductor\n---\n",
    )

    assert call_order.index("terminate") > call_order.index("merge")
    assert call_order.index("terminate") > call_order.index("promote")


@pytest.mark.asyncio
async def test_successor_admitted_then_terminates(
    tmp_path: Path,
    bus_db: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _seed_ephemeral_bus_thread(bus_db, execution_id="exec-succ-1")
    req = _req(
        dispatch_id="cond-succ-1",
        execution_id="exec-succ-1",
        thread_id=thread_id,
    )
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body=_ROW_HOP_BODY, call_order=call_order
    )

    async def _stamp_successor(*, dispatch_id: str) -> None:
        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={"hop_successor": "succ-hop-99"},
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
        "maybe_fire_conductor_hop_reactor",
        _stamp_successor,
    )
    bus = await _real_bus_client(bus_db, monkeypatch)

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome(_ROW_HOP_BODY),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=_controller(),
        packet_text="---\ncontract: conductor\n---\n",
    )

    assert "terminate" in call_order
    assert call_order.index("terminate") > call_order.index("promote")
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("hop_successor") == "succ-hop-99"


@pytest.mark.asyncio
async def test_ordinary_completion_still_terminates(
    tmp_path: Path,
    bus_db: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = _seed_ephemeral_bus_thread(bus_db, execution_id="exec-ord-1")
    req = _req(
        dispatch_id="cond-ord-1",
        execution_id="exec-ord-1",
        thread_id=thread_id,
    )
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch, closeout_body=_ORDINARY_BODY, call_order=call_order
    )
    bus = await _real_bus_client(bus_db, monkeypatch)

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome(_ORDINARY_BODY),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=_controller(),
        packet_text="---\ncontract: conductor\n---\n",
    )

    assert call_order == ["merge", "promote", "terminate"]


@pytest.mark.asyncio
async def test_merge_raise_still_records_merge_then_promote(
    tmp_path: Path,
    bus_db: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """friction 37489: raise-path still records merge before promote."""
    thread_id = _seed_ephemeral_bus_thread(
        bus_db, execution_id="exec-merge-raise-order"
    )
    req = _req(
        dispatch_id="cond-merge-raise-order",
        execution_id="exec-merge-raise-order",
        thread_id=thread_id,
    )
    _admit_running_conductor(req, tmp_path)
    call_order: list[str] = []
    _install_closeout_stubs(
        monkeypatch,
        closeout_body=_ORDINARY_BODY,
        call_order=call_order,
        merge_raises=RuntimeError("hop merge boom"),
    )
    bus = await _real_bus_client(bus_db, monkeypatch)

    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=_outcome(_ORDINARY_BODY),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref="todo:conductor-closeout-completes-open-mission",
        controller=_controller(),
        packet_text="---\ncontract: conductor\n---\n",
    )

    assert call_order[:2] == ["merge", "promote"]
    assert call_order == ["merge", "promote", "terminate"]


@pytest.mark.asyncio
async def test_park_finalize_does_not_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_closeout.park_finalize import (
        finalize_parked,
    )
    from services.git_integration_worker.cursor_sdk_park_for_restart import (
        ParkMark,
        _marks,
    )

    req = _req(dispatch_id="fin-park-g3", execution_id="exec-park-g3", thread_id="9010")
    _admit_running_conductor(req, tmp_path)
    mark = ParkMark(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        intent_id="intent-g3",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-01T00:00:00Z",
        method="run_cancel",
        marked_at=0.0,
    )
    _marks[req.dispatch_id] = mark
    terminate_spy = AsyncMock()
    monkeypatch.setattr(route_mod, "_terminate_link", terminate_spy)
    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", AsyncMock())
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_partial_harvest_on_park",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.mark_parked",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "merge_conductor_closeout_hop_authority",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "emit_sdk_park_parked",
        lambda **_kw: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize."
        "terminal_emitted",
        lambda *_a, **_k: True,
    )
    bus = MagicMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 3})
    )

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.park_finalize.finalize_parked",
        wraps=finalize_parked,
    ) as spy_finalize:
        await spy_finalize(
            req=req,
            source_repo=tmp_path / "repo",
            bus=bus,
            reply_to="dispatch",
            controller=_controller(),
            mark=mark,
            outcome=_outcome(""),
            exc=None,
        )

    spy_finalize.assert_awaited_once()
    terminate_spy.assert_not_awaited()
