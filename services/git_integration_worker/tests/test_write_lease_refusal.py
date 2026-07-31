"""Write-lease refusal fence — R1 L1 (25956 / release-before-admit)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    WriteLeaseHeld,
    _connect,
)
from services.git_integration_worker.cursor_sdk_nest_depth import NestParentNotLive
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admission(req: CursorDispatchRequest) -> CursorDispatchResponse:
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="composer-2.5",
    )


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str = "/repo",
    lease_key: str | None = None,
    read_only: bool = False,
    nest_under: str | None = None,
    refuse_if_lease_held: bool = False,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo=source_repo,
        lease_key=lease_key or source_repo,
        contract="implement",
        worker_instance="worker-a",
        read_only=read_only,
        nest_under=nest_under,
        refuse_if_lease_held=refuse_if_lease_held,
    )


def test_ac1_refuse_if_lease_held_raises_and_inserts_no_row() -> None:
    """AC1: typed refusal inside admit txn — zero rows for dispatch_id."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="holder"), lease_key=key)
    child = _req(dispatch_id="child-would-queue")

    with pytest.raises(WriteLeaseHeld) as exc_info:
        _admit(
            ledger,
            child,
            lease_key=key,
            refuse_if_lease_held=True,
        )
    assert exc_info.value.holder_dispatch_id == "holder"

    with _connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-would-queue",),
        ).fetchone()
    assert row is None


def test_ac2_without_flag_retains_queued_behavior() -> None:
    """AC2: back-compat — no flag ⇒ silent queue ticket."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="holder-2"), lease_key=key)
    queued = _admit(ledger, _req(dispatch_id="peer-2"), lease_key=key)
    assert queued is not None
    assert queued.status == "queued"

    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("peer-2",),
        ).fetchone()
    assert row is not None
    assert row["status"] == "queued"


def test_ac4_legitimate_nest_still_admits_and_parks_parent() -> None:
    """AC4: nest_under == live holder — fence did not break B1 path."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="parent-nest"), lease_key=key)
    result = _admit(
        ledger,
        _req(dispatch_id="child-nest"),
        lease_key=key,
        nest_under="parent-nest",
        refuse_if_lease_held=True,
    )
    assert result is None

    with _connect() as conn:
        parent = conn.execute(
            "SELECT status, park_child_dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='parent-nest'"
        ).fetchone()
        child = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id='child-nest'"
        ).fetchone()
    assert parent is not None
    assert parent["status"] == "parked_waiting"
    assert parent["park_child_dispatch_id"] == "child-nest"
    assert child is not None
    assert child["status"] == "admitted"


def test_ac5_read_only_admits_while_write_holder_live() -> None:
    """AC5: read_only path untouched — no queue, no refusal."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="write-holder"), lease_key=key)
    result = _admit(
        ledger,
        _req(dispatch_id="read-only-peer", thread_id="t-read"),
        lease_key=key,
        read_only=True,
        refuse_if_lease_held=True,
    )
    assert result is None

    with _connect() as conn:
        row = conn.execute(
            "SELECT status, read_only FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='read-only-peer'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "admitted"
    assert row["read_only"] == 1


def test_refuse_rejects_stale_nest_under() -> None:
    """nest_under mismatch still raises NestParentNotLive even with refuse flag."""
    ledger = CursorDispatchLedger.instance()
    key = "/repo"

    _admit(ledger, _req(dispatch_id="live-holder"), lease_key=key)
    with pytest.raises(NestParentNotLive):
        _admit(
            ledger,
            _req(dispatch_id="bad-nest"),
            lease_key=key,
            nest_under="wrong-parent",
            refuse_if_lease_held=True,
        )
