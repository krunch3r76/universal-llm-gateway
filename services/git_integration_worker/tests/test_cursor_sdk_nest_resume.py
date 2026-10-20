"""Freeform parent that ends while a nest child is live must resume, not close."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store.cursor_sdk_dispatch_turn import (
    is_cursor_sdk_advisory_subject,
    is_cursor_sdk_dispatch_terminal_subject,
)

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_nest_resume import (
    NEST_PARENT_RUN_EXITED,
    admit_nest_parent_resume,
    claim_nest_parent_exit,
    defer_finished_parent_for_live_nest,
    nest_parked_subject,
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
        patch.object(route_mod, "force_release_sdk_dispatch_slot", AsyncMock()),
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
    assert ok is False
    with ledger._connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent'"
        ).fetchone()
    assert parent["status"] == "cancelled"
    assert parent["park_child_dispatch_id"] is None
