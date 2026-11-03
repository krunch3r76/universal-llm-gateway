"""Freeform parent that ends while a nest child is live must resume, not close."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store.cursor_sdk_dispatch_turn import (
    is_cursor_sdk_advisory_subject,
    is_cursor_sdk_dispatch_terminal_subject,
)
from universal_concurrency import FifoCapacityGate

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    sdk_busy_status_envelope,
    sdk_dispatch_gate_holders,
)
from services.git_integration_worker.cursor_sdk_nest_resume import (
    NEST_PARENT_RUN_EXITED,
    _resume_request,
    admit_nest_parent_resume,
    claim_nest_parent_exit,
    claim_parked_parent_resume,
    defer_finished_parent_for_live_nest,
    nest_parked_subject,
)
from services.git_integration_worker.cursor_sdk_park import (
    _fail_stranded_parked_parent,
    reclaim_orphan_holder,
    release_or_restore_for_child_sync,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(dispatch_id: str) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id="18533",
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message=f"msg-{dispatch_id}",
    )


def _admit(
    ledger: CursorDispatchLedger,
    dispatch_id: str,
    *,
    contract: str,
    nest_under: str | None = None,
    work_key: str | None = None,
    lease_key: str | None = None,
) -> None:
    req = _req(dispatch_id)
    if nest_under:
        req = req.model_copy(update={"nest_under": nest_under})
    wk = work_key or f"friction:{dispatch_id}"
    lease = lease_key or f"/repo/{dispatch_id}"
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="pipeline:cursor-paste-resolve",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="grok-4.7",
        ),
        contract=contract,
        source_repo=lease,
        lease_key=lease,
        work_key=wk,
        source_ref=wk,
        nest_under=nest_under,
    )


def test_nest_resume_builder_recovers_request_id() -> None:
    """Breaks when nest-resume child drops explicit Stargate request_id from parent."""
    parent_id = "660e8400-e29b-41d4-a716-446655440001"
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, parent_id, contract="freeform")
    ledger.merge_record_json(
        dispatch_id=parent_id,
        patch={
            "request_id": "rid",
            "dispatch_id": parent_id,
        },
    )
    req = _resume_request(parent_id, child_id="nested-child")
    assert req is not None
    assert req.dispatch_id == f"{parent_id}-nr1"
    assert req.request_id == "rid"


def test_parked_subject_is_advisory_not_terminal() -> None:
    subject = nest_parked_subject(dispatch_id="parent", child_id="child")
    assert is_cursor_sdk_advisory_subject(subject)
    assert not is_cursor_sdk_dispatch_terminal_subject(subject)


def test_claim_keeps_freeform_parent_parked_while_child_runs() -> None:
    """Breaks when parent completion is allowed to overwrite a live nest park."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "parent", contract="freeform")
    _admit(
        ledger,
        "child",
        contract="freeform",
        nest_under="parent",
        work_key="friction:parent",
        lease_key="/repo/parent",
    )
    assert claim_nest_parent_exit("parent") == "child"
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent'"
        ).fetchone()
    assert row["status"] == "parked_waiting"
    assert NEST_PARENT_RUN_EXITED in row["record_json"]


def test_conductor_excluded_from_nest_resume_of() -> None:
    """Alias AC: conductors never take ``resume_of`` from nest parent resume."""
    test_claim_skips_conductor_and_terminal_child()


def test_claim_skips_conductor_and_terminal_child() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "conductor", contract="conductor")
    _admit(
        ledger,
        "child-c",
        contract="implement",
        nest_under="conductor",
        work_key="friction:conductor",
        lease_key="/repo/conductor",
    )
    assert claim_nest_parent_exit("conductor") is None
    ledger.mark_terminal(dispatch_id="child-c", terminal_status="completed")
    ledger.mark_terminal(dispatch_id="conductor", terminal_status="cancelled")
    _admit(ledger, "parent", contract="freeform")
    _admit(
        ledger,
        "child-done",
        contract="freeform",
        nest_under="parent",
        work_key="friction:parent",
        lease_key="/repo/parent",
    )
    ledger.mark_terminal(dispatch_id="child-done", terminal_status="completed")
    assert claim_nest_parent_exit("parent") is None


@pytest.mark.asyncio
async def test_defer_posts_parked_and_skips_terminal_closeout() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "parent", contract="freeform")
    _admit(
        ledger,
        "child",
        contract="freeform",
        nest_under="parent",
        work_key="friction:parent",
        lease_key="/repo/parent",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock()
    held = await defer_finished_parent_for_live_nest(
        dispatch_id="parent",
        thread_id="18533",
        reply_to="pipeline:cursor-paste-resolve",
        bus=bus,
        closeout_body="status: partial — waiting on child",
    )
    assert held is True
    subject = bus.reply.call_args.kwargs["subject"]
    assert "PARKED" in subject
    assert "CLOSEOUT" not in subject


@pytest.mark.asyncio
async def test_child_terminal_admits_resume_of_exited_parent() -> None:
    """Breaks when a finished nest child leaves a freeform parent unresumed."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "parent", contract="freeform")
    _admit(
        ledger,
        "child",
        contract="freeform",
        nest_under="parent",
        work_key="friction:parent",
        lease_key="/repo/parent",
    )
    assert claim_nest_parent_exit("parent") == "child"
    ledger.mark_terminal(dispatch_id="child", terminal_status="completed")
    admitted = AsyncMock()
    admitted.return_value = type("Resp", (), {"status_code": 200})()

    class _Ctl:
        worker_id = "w"

        def is_draining(self) -> bool:
            return False

        def maybe_activate_armed_drain(self, *, dispatch_id: str) -> None:
            del dispatch_id

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
            return_value="parent_still_live",
        ),
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            admitted,
        ),
        patch.object(route_mod, "release_or_restore_for_child", AsyncMock()) as rel,
    ):
        await route_mod._mark_terminal_and_promote(
            dispatch_id="child",
            terminal_status="completed",
            controller=_Ctl(),
            emit_tag="TEST",
        )
    rel.assert_not_called()
    admitted.assert_awaited()
    req = admitted.await_args.args[0]
    assert req.resume_of == "parent"
    assert req.dispatch_id == "parent-nr1"
    assert req.execution_id == "exec-parent"
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent'"
        ).fetchone()
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='child'"
        ).fetchone()
    assert parent["status"] == "cancelled"
    assert parent["park_child_dispatch_id"] is None
    assert child["status"] == "completed"


@pytest.mark.asyncio
async def test_ineligible_parent_is_sealed_and_not_admitted() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "parent", contract="freeform")
    _admit(
        ledger,
        "child",
        contract="freeform",
        nest_under="parent",
        work_key="friction:parent",
        lease_key="/repo/parent",
    )
    claim_nest_parent_exit("parent")
    with patch(
        "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
        return_value="sdk_agent_id_missing",
    ):
        ok = await admit_nest_parent_resume(
            "parent",
            child_id="child",
            controller=object(),
            cfg=object(),
        )
    assert ok.outcome == "refused"
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent'"
        ).fetchone()
    assert parent["status"] == "cancelled"
    assert parent["park_child_dispatch_id"] is None


class _TerminalCtl:
    worker_id = "w"

    def is_draining(self) -> bool:
        return False

    def is_armed(self) -> bool:
        return False

    def maybe_activate_armed_drain(self, *, dispatch_id: str) -> None:
        del dispatch_id


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_id", ["parent-exit", "parent-exit-r1"])
async def test_cleanup_then_terminal_admits_resume_of_exited_parent(
    parent_id: str,
) -> None:
    """Breaks when child cleanup restores an exited parent before resume lookup.

    Production order is the worker finally (``release_or_restore_for_child_sync``)
    and then ``_mark_terminal_and_promote``. Nothing in that pair is patched.
    ``parent-exit-r1`` is a ``giw_park_resume`` parent: the parked row id itself
    ends in ``-r1``, and the resume must key off that row.
    """
    child_id = f"{parent_id}-child"
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, parent_id, contract="freeform")
    _admit(
        ledger,
        child_id,
        contract="freeform",
        nest_under=parent_id,
        work_key=f"friction:{parent_id}",
        lease_key=f"/repo/{parent_id}",
    )
    assert claim_nest_parent_exit(parent_id) == child_id
    loop = asyncio.get_running_loop()

    def _cleanup() -> str:
        return release_or_restore_for_child_sync(loop, dispatch_id=child_id)

    await asyncio.to_thread(_cleanup)
    from services.git_integration_worker.routes.admin import dispatch_status

    status_row = await dispatch_status(None, dispatch_id=parent_id)
    assert status_row["status"] == "parked_waiting"
    assert status_row["park_kind"] == NEST_PARENT_RUN_EXITED
    assert status_row["park"]["park_kind"] == NEST_PARENT_RUN_EXITED
    admitted = AsyncMock()
    admitted.return_value = type("Resp", (), {"status_code": 200})()
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
        admitted,
    ):
        await route_mod._mark_terminal_and_promote(
            dispatch_id=child_id,
            terminal_status="completed",
            controller=_TerminalCtl(),
            emit_tag="TEST",
        )
    admitted.assert_awaited()
    req = admitted.await_args.args[0]
    assert req.resume_of == parent_id
    assert req.dispatch_id == f"{parent_id}-nr1"
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "cancelled"


def _seed_exited_parent(
    ledger: CursorDispatchLedger, *, parent_id: str, child_id: str, child_terminal: str
) -> None:
    """Park a freeform parent, stamp exit, then terminal the child."""
    _admit(ledger, parent_id, contract="freeform")
    _admit(
        ledger,
        child_id,
        contract="freeform",
        nest_under=parent_id,
        work_key=f"friction:{parent_id}",
        lease_key=f"/repo/{parent_id}",
    )
    assert claim_nest_parent_exit(parent_id) == child_id
    ledger.mark_terminal(dispatch_id=child_id, terminal_status=child_terminal)


@pytest.mark.asyncio
async def test_terminal_and_reclaim_admit_exited_parent_once() -> None:
    """Breaks when the terminal path and the orphan sweep both admit resume_of.

    An exited parent stays parked_waiting after child cleanup. Both resumers
    must share one claim, so a concurrent pair yields one admit.
    """
    parent_id = "parent-race"
    child_id = "parent-race-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    admitted = AsyncMock()
    admitted.return_value = type("Resp", (), {"status_code": 200})()
    # Hold reclaim inside child cleanup until the terminal path has also
    # observed the parked parent. A gather without this overlap lets the
    # first resumer seal the row before the second reads it.
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()

    from services.git_integration_worker import cursor_sdk_park as park_mod

    real_async_release = park_mod.release_or_restore_for_child

    async def _blocked_release(*, dispatch_id: str) -> str:
        cleanup_entered.set()
        await release_cleanup.wait()
        return await real_async_release(dispatch_id=dispatch_id)

    async def _reclaim() -> None:
        await reclaim_orphan_holder(
            ledger,
            dispatch_id=parent_id,
            controller=_TerminalCtl(),
            cfg=object(),
        )

    with (
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            admitted,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
            lambda *a, **k: "parent_still_live",
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_park._stop_orphan_bridge",
            AsyncMock(return_value=True),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_park.release_or_restore_for_child",
            _blocked_release,
        ),
    ):
        reclaim_task = asyncio.create_task(_reclaim())
        await asyncio.wait_for(cleanup_entered.wait(), timeout=2)
        await route_mod._mark_terminal_and_promote(
            dispatch_id=child_id,
            terminal_status="completed",
            controller=_TerminalCtl(),
            emit_tag="TEST",
        )
        release_cleanup.set()
        await reclaim_task
    assert admitted.await_count == 1
    req = admitted.await_args.args[0]
    assert req.resume_of == parent_id
    assert req.dispatch_id == f"{parent_id}-nr1"
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] != "parked_waiting"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "child_terminal,reap_only,pass_controller",
    [
        ("failed", False, True),
        ("cancelled", False, True),
        ("completed", False, False),
        ("completed", True, True),
    ],
)
async def test_exited_parent_not_stranded_when_resume_not_admitted(
    child_terminal: str, reap_only: bool, pass_controller: bool
) -> None:
    """Breaks when a failed, cancelled, or unresumed exited parent stays parked."""
    parent_id = f"parent-strand-{child_terminal}-{reap_only}-{pass_controller}"
    child_id = f"{parent_id}-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger,
        parent_id=parent_id,
        child_id=child_id,
        child_terminal=child_terminal,
    )
    admitted = AsyncMock()
    admitted.return_value = type("Resp", (), {"status_code": 200})()
    with (
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            admitted,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_park._stop_orphan_bridge",
            AsyncMock(return_value=True),
        ),
    ):
        await reclaim_orphan_holder(
            ledger,
            dispatch_id=parent_id,
            controller=_TerminalCtl() if pass_controller else None,
            cfg=object() if pass_controller else None,
            reap_only=reap_only,
        )
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] in {"failed", "cancelled", "completed"}
    assert parent["status"] != "parked_waiting"


def _isolate_capacity_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """One limit-1 gate so a leaked resume slot blocks the next acquire."""
    isolated = FifoCapacityGate(limit=1, gate_id="friction-41842")
    gates = {"standard": isolated, "operator": isolated}
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._STANDARD_GATE",
        isolated,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._OPERATOR_GATE",
        isolated,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._LANE_GATES",
        gates,
    )


def _insert_queued(
    ledger: CursorDispatchLedger, *, dispatch_id: str, lease_key: str
) -> None:
    req = _req(dispatch_id)
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, lease_key, "
            " read_only, queued_at) "
            "VALUES (?, ?, ?, ?, ?, 1, 'queued', ?, ?, ?, 0, ?)",
            (
                dispatch_id,
                ledger.fingerprint(req),
                req.thread_id,
                req.execution_id,
                "grok-4.7",
                json.dumps({"model": req.model, "message": req.message}),
                lease_key,
                lease_key,
                "2026-11-03T00:00:00+00:00",
            ),
        )


@pytest.mark.asyncio
async def test_terminal_loser_does_not_promote_queued_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when the claim loser releases the lease and promotes FIFO.

    The other resumer has already cancelled the parent and has not inserted
    the resume row. Promoting the queued writer in that window stalls it on
    the slot the resume still holds.
    """
    parent_id = "parent-loser"
    child_id = "parent-loser-child"
    lease_key = f"/repo/{parent_id}"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    _insert_queued(ledger, dispatch_id="queued-writer", lease_key=lease_key)
    released: list[str] = []

    def _prune_claims_first(*_args: object, **_kwargs: object) -> None:
        claim_parked_parent_resume(
            parent_id,
            park_child_id=child_id,
            resume_id=f"{parent_id}-nr1",
        )

    def _record_release(*_args: object, **kwargs: object) -> None:
        released.append(str(kwargs.get("dispatch_id") or ""))

    monkeypatch.setattr(
        route_mod, "maybe_prune_worktree_on_terminal", _prune_claims_first
    )
    monkeypatch.setattr(route_mod, "emit_write_lease_released", _record_release)
    monkeypatch.setattr(route_mod, "_start_promoted_dispatch", AsyncMock())
    admitted = AsyncMock()
    admitted.return_value = type("Resp", (), {"status_code": 200})()
    with (
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            admitted,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
            return_value="parent_still_live",
        ),
    ):
        await route_mod._mark_terminal_and_promote(
            dispatch_id=child_id,
            terminal_status="completed",
            controller=_TerminalCtl(),
            emit_tag="TEST",
        )
    assert admitted.await_count == 0
    assert released == []
    with ledger._connect() as conn:
        queued = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='queued-writer'"
        ).fetchone()
    assert queued["status"] == "queued"


@pytest.mark.asyncio
async def test_fail_stranded_skips_bridge_when_parent_already_claimed() -> None:
    """Breaks when a lost reap stops the bridge of a parent already resumed."""
    parent_id = "parent-stranded-claimed"
    child_id = "parent-stranded-claimed-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    claim = claim_parked_parent_resume(
        parent_id,
        park_child_id=child_id,
        resume_id=f"{parent_id}-nr1",
    )
    assert claim.outcome == "claimed"
    stop = AsyncMock(return_value=True)
    with patch(
        "services.git_integration_worker.cursor_sdk_park._stop_orphan_bridge",
        stop,
    ):
        result = await _fail_stranded_parked_parent(
            ledger,
            dispatch_id=parent_id,
            child_id=child_id,
            lease_key=f"/repo/{parent_id}",
        )
    stop.assert_not_called()
    assert result.lease_key is None
    assert result.resume_admitted is False


@pytest.mark.asyncio
async def test_ineligible_resume_releases_handed_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when an ineligible claim leaves the slot on the resume id."""
    _isolate_capacity_gate(monkeypatch)
    parent_id = "parent-slot"
    child_id = "parent-slot-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    await acquire_sdk_dispatch_slot(dispatch_id=parent_id)
    with patch(
        "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
        return_value="sdk_agent_id_missing",
    ):
        ok = await admit_nest_parent_resume(
            parent_id,
            child_id=child_id,
            controller=object(),
            cfg=object(),
        )
    assert ok.outcome == "refused"
    assert f"{parent_id}-nr1" not in sdk_dispatch_gate_holders()
    await asyncio.wait_for(
        acquire_sdk_dispatch_slot(dispatch_id="waiter-ineligible"),
        timeout=1,
    )


@pytest.mark.asyncio
async def test_admit_raise_releases_handed_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when admit_cursor_dispatch raising after claim leaks the slot."""
    _isolate_capacity_gate(monkeypatch)
    parent_id = "parent-raise"
    child_id = "parent-raise-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    await acquire_sdk_dispatch_slot(dispatch_id=parent_id)
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
            return_value="parent_still_live",
        ),
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            AsyncMock(side_effect=RuntimeError("admit blew up")),
        ),
    ):
        with pytest.raises(RuntimeError, match="admit blew up"):
            await admit_nest_parent_resume(
                parent_id,
                child_id=child_id,
                controller=object(),
                cfg=object(),
            )
    assert f"{parent_id}-nr1" not in sdk_dispatch_gate_holders()
    await asyncio.wait_for(
        acquire_sdk_dispatch_slot(dispatch_id="waiter-raise"),
        timeout=1,
    )


@pytest.mark.asyncio
async def test_hand_resume_rejected_keeps_parent_parked_and_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when a hand resume_of rejected after the claim burns the parent.

    The claim used to run before eligibility. A thread mismatch then left the
    parent cancelled and the capacity slot on the rejected resume id.
    """
    _isolate_capacity_gate(monkeypatch)
    parent_id = "parent-hand"
    child_id = "parent-hand-child"
    hand_id = "hand-resume-1"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    hub = str(route_mod._CONFIG.source_repo)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_repo=?, lease_key=? "
            "WHERE dispatch_id=?",
            (hub, hub, parent_id),
        )
    await acquire_sdk_dispatch_slot(dispatch_id=parent_id)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"cursor_auth_source": ""},
    )
    monkeypatch.setattr(route_mod, "is_not_entitled", lambda *_a, **_k: False)
    monkeypatch.setattr(route_mod, "_branch_debt_refusal", lambda _req: None)
    monkeypatch.setattr(
        route_mod,
        "select_lane",
        lambda **_k: ("B", [], "test"),
    )
    req = CursorDispatchRequest(
        thread_id="other-thread",
        model="cursor/composer-2.5",
        dispatch_id=hand_id,
        execution_id="exec-hand",
        message="hand resume",
        resume_of=parent_id,
        work_key="friction:hand-resume",
        handoff_contract="freeform",
        caller_agent="pipeline:cursor-paste-resolve",
    )
    response = await route_mod.admit_cursor_dispatch(
        req,
        cfg=route_mod._CONFIG,
        controller=_TerminalCtl(),
    )
    assert response.status_code == 422
    body = response.body.decode()
    assert "thread_mismatch" in body or "CURSOR_RESUME_INELIGIBLE" in body
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == child_id
    assert sdk_dispatch_gate_holders() == frozenset({parent_id})


@pytest.mark.asyncio
async def test_hand_resume_admit_error_restores_parent_and_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when ledger.admit raising after the hand claim burns the parent."""
    from pathlib import Path

    from services.git_integration_worker.cursor_dispatch_ledger import WriteLeaseHeld
    from services.git_integration_worker.cursor_sdk_worktree import AdmitBindingResult

    _isolate_capacity_gate(monkeypatch)
    parent_id = "parent-hand-err"
    child_id = "parent-hand-err-child"
    hand_id = "hand-resume-err"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger, parent_id=parent_id, child_id=child_id, child_terminal="completed"
    )
    hub = route_mod._CONFIG.source_repo
    hub_path = hub if isinstance(hub, Path) else Path(hub)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_repo=?, lease_key=? "
            "WHERE dispatch_id=?",
            (str(hub_path), str(hub_path), parent_id),
        )
    await acquire_sdk_dispatch_slot(dispatch_id=parent_id)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"cursor_auth_source": ""},
    )
    monkeypatch.setattr(route_mod, "is_not_entitled", lambda *_a, **_k: False)
    monkeypatch.setattr(route_mod, "_branch_debt_refusal", lambda _req: None)
    monkeypatch.setattr(route_mod, "select_lane", lambda **_k: ("A", [], "test"))
    monkeypatch.setattr(
        route_mod,
        "resolve_admit_binding",
        lambda **_k: AdmitBindingResult(
            workspace=hub_path,
            lease_key=str(hub_path),
            binding_kind="lane_a",
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
        lambda *_a, **_k: None,
    )

    def _lease_held(self, **_kwargs: object) -> None:
        del self
        raise WriteLeaseHeld(
            lease_key=str(hub_path),
            holder_dispatch_id="holder",
            holder_thread_id="18533",
        )

    monkeypatch.setattr(CursorDispatchLedger, "admit", _lease_held)
    req = CursorDispatchRequest(
        thread_id="18533",
        model="cursor/composer-2.5",
        dispatch_id=hand_id,
        execution_id="exec-hand-err",
        message="hand resume error",
        resume_of=parent_id,
        work_key="friction:hand-resume-err",
        handoff_contract="freeform",
        caller_agent="pipeline:cursor-paste-resolve",
    )
    response = await route_mod.admit_cursor_dispatch(
        req,
        cfg=route_mod._CONFIG,
        controller=_TerminalCtl(),
    )
    assert response.status_code == 409
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == child_id
    assert hand_id not in sdk_dispatch_gate_holders()
    assert parent_id not in sdk_dispatch_gate_holders()


def test_busy_status_ignores_exited_parked_parent() -> None:
    """Breaks when the sole exited parked row is reported as the busy holder."""
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(
        ledger,
        parent_id="parent-busy",
        child_id="parent-busy-child",
        child_terminal="completed",
    )
    envelope = sdk_busy_status_envelope()
    holder = envelope["active_holder"]
    assert holder is None or holder.get("dispatch_id") != "parent-busy"
    assert all(
        row.get("dispatch_id") != "parent-busy" for row in envelope["active_holders"]
    )
