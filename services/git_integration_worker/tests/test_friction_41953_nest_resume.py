"""Nest resume must not burn a parked parent, and a refused sweep must keep the lease.

Callers are the nest terminal path and the stale-lease sweeper. A retryable
admit failure or a draining controller has to leave the parent parked_waiting.
A permanent refusal has to return the lease key so the sweeper can promote.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_nest_resume import (
    claim_nest_parent_exit,
)
from services.git_integration_worker.cursor_sdk_park import reclaim_orphan_holder
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


class _Ctl:
    """Admission stand-in with an explicit drain and armed flag."""

    worker_id = "w"

    def __init__(self, *, draining: bool = False, armed: bool = False) -> None:
        self._draining = draining
        self._armed = armed

    def is_draining(self) -> bool:
        return self._draining

    def is_armed(self) -> bool:
        return self._armed

    def maybe_activate_armed_drain(self, *, dispatch_id: str) -> None:
        del dispatch_id


def _req(dispatch_id: str) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id="22153",
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


def _seed_exited_parent(
    ledger: CursorDispatchLedger, *, parent_id: str, child_id: str
) -> str:
    """Park a freeform parent on an exited nest and return its lease key."""
    lease_key = f"/repo/{parent_id}"
    _admit(ledger, parent_id, contract="freeform", lease_key=lease_key)
    _admit(
        ledger,
        child_id,
        contract="freeform",
        nest_under=parent_id,
        work_key=f"friction:{parent_id}",
        lease_key=lease_key,
    )
    assert claim_nest_parent_exit(parent_id) == child_id
    return lease_key


def _parent_row(ledger: CursorDispatchLedger, parent_id: str):
    with ledger._connect() as conn:
        return conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()


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
@pytest.mark.parametrize("draining,armed", [(True, False), (False, True)])
async def test_drain_or_armed_terminal_leaves_parent_parked(
    draining: bool, armed: bool
) -> None:
    """Breaks when a draining or armed terminal cancels the exited parent."""
    parent_id = f"parent-drain-{draining}-{armed}"
    child_id = f"{parent_id}-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(ledger, parent_id=parent_id, child_id=child_id)
    admitted = AsyncMock(return_value=type("Resp", (), {"status_code": 200})())
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
        admitted,
    ):
        await route_mod._mark_terminal_and_promote(
            dispatch_id=child_id,
            terminal_status="completed",
            controller=_Ctl(draining=draining, armed=armed),
            emit_tag="TEST",
        )
    admitted.assert_not_called()
    parent = _parent_row(ledger, parent_id)
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == child_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,expect_parked",
    [
        (503, b"", True),
        (409, b'{"code":"CURSOR_WRITE_LEASE_HELD"}', True),
        (422, b'{"code":"CURSOR_RESUME_INELIGIBLE"}', False),
    ],
)
async def test_retryable_admit_refusal_restores_parked_parent(
    status: int, body: bytes, expect_parked: bool
) -> None:
    """Breaks when a 503 or lease-held 409 leaves the parent cancelled."""
    parent_id = f"parent-http-{status}"
    child_id = f"{parent_id}-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(ledger, parent_id=parent_id, child_id=child_id)
    admitted = AsyncMock(
        return_value=type("Resp", (), {"status_code": status, "body": body})()
    )
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
        admitted,
    ):
        await route_mod._mark_terminal_and_promote(
            dispatch_id=child_id,
            terminal_status="completed",
            controller=_Ctl(),
            emit_tag="TEST",
        )
    parent = _parent_row(ledger, parent_id)
    if expect_parked:
        assert parent["status"] == "parked_waiting"
        assert parent["park_child_dispatch_id"] == child_id
    else:
        assert parent["status"] == "cancelled"
        assert parent["park_child_dispatch_id"] is None


@pytest.mark.asyncio
async def test_admit_raise_restores_parked_parent() -> None:
    """Breaks when admit_cursor_dispatch raising burns the claimed parent."""
    parent_id = "parent-raise"
    child_id = "parent-raise-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(ledger, parent_id=parent_id, child_id=child_id)
    with patch(
        "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
        AsyncMock(side_effect=RuntimeError("admit blew up")),
    ):
        with pytest.raises(RuntimeError, match="admit blew up"):
            await route_mod._mark_terminal_and_promote(
                dispatch_id=child_id,
                terminal_status="completed",
                controller=_Ctl(),
                emit_tag="TEST",
            )
    parent = _parent_row(ledger, parent_id)
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == child_id


@pytest.mark.asyncio
async def test_sweeper_retryable_refusal_leaves_parent_parked() -> None:
    """Breaks when a sweeper 503 cancels the parent and drops its lease key."""
    parent_id = "parent-sweep-503"
    child_id = "parent-sweep-503-child"
    ledger = CursorDispatchLedger.instance()
    _seed_exited_parent(ledger, parent_id=parent_id, child_id=child_id)
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="completed")
    with (
        patch(
            "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
            AsyncMock(
                return_value=type("Resp", (), {"status_code": 503, "body": b""})()
            ),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_park._stop_orphan_bridge",
            AsyncMock(return_value=True),
        ),
    ):
        reclaimed = await reclaim_orphan_holder(
            ledger,
            dispatch_id=parent_id,
            controller=_Ctl(),
            cfg=object(),
        )
    parent = _parent_row(ledger, parent_id)
    assert parent["status"] == "parked_waiting"
    assert reclaimed.lease_key is None
    assert reclaimed.resume_admitted is False


@pytest.mark.asyncio
async def test_sweeper_ineligible_refusal_returns_lease_key() -> None:
    """Breaks when a refused nest resume drops the lease key on the sweeper."""
    parent_id = "parent-sweep-ineligible"
    child_id = "parent-sweep-ineligible-child"
    ledger = CursorDispatchLedger.instance()
    lease_key = _seed_exited_parent(ledger, parent_id=parent_id, child_id=child_id)
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="completed")
    _insert_queued(ledger, dispatch_id="queued-writer", lease_key=lease_key)
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_resume.resume_eligibility_reason",
            return_value="sdk_agent_id_missing",
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_park._stop_orphan_bridge",
            AsyncMock(return_value=True),
        ),
    ):
        reclaimed = await reclaim_orphan_holder(
            ledger,
            dispatch_id=parent_id,
            controller=_Ctl(),
            cfg=object(),
        )
    assert reclaimed.lease_key == lease_key
    assert reclaimed.resume_admitted is False
    parent = _parent_row(ledger, parent_id)
    assert parent["status"] == "cancelled"
    with ledger._connect() as conn:
        queued = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='queued-writer'"
        ).fetchone()
    assert queued["status"] == "queued"
