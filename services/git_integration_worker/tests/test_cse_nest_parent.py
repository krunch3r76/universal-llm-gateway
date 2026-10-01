"""Nest resolution for ``cse:`` typed parents."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_bundles.holder_strings import format_nest_under_cse

from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    upsert_holder,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    NestParentNotLive,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_CSE_URL = "https://claude.ai/cowork/cse_nest1"


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()






def test_admit_cse_nest_skips_sdk_park(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-n",
            lane_thread_id="11667",
        )
        conn.commit()
    req = CursorDispatchRequest(
        thread_id="11668",
        model="cursor/composer-2.5",
        dispatch_id="child-nest-1",
        execution_id="exec-child",
        message="nested implement",
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="cursor/composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-auto",
        resolved_model="cursor/composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, nest_under FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "admitted"
    assert row["nest_under"] == format_nest_under_cse("cse_nest1")


def test_admit_cse_missing_parent_fail_closed(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
    req = CursorDispatchRequest(
        thread_id="11668",
        model="cursor/composer-2.5",
        dispatch_id="child-miss",
        execution_id="exec-miss",
        message="nested",
        nest_under="cse:cse_missing",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="cursor/composer-2.5",
    )
    with pytest.raises(NestParentNotLive):
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent="cursor-auto",
            resolved_model="cursor/composer-2.5",
            admission=admission,
            source_repo="/tmp/repo",
            nest_under="cse:cse_missing",
        )


def test_admit_cse_nest_queues_when_unrelated_writer_holds_lease(
    ledger: CursorDispatchLedger,
) -> None:
    lease_key = "/tmp/repo-r7"
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-n",
            lane_thread_id="11667",
        )
        conn.commit()
    holder_req = CursorDispatchRequest(
        thread_id="11667",
        model="cursor/composer-2.5",
        dispatch_id="unrelated-writer",
        execution_id="exec-holder",
        message="unrelated writer",
    )
    holder_admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=holder_req.dispatch_id,
        thread_id=holder_req.thread_id,
        model_id="cursor/composer-2.5",
    )
    ledger.admit(
        req=holder_req,
        fingerprint=ledger.fingerprint(holder_req),
        execution_id=holder_req.execution_id,
        caller_agent="cursor-auto",
        resolved_model="cursor/composer-2.5",
        admission=holder_admission,
        source_repo=lease_key,
        lease_key=lease_key,
    )
    child_req = CursorDispatchRequest(
        thread_id="11668",
        model="cursor/composer-2.5",
        dispatch_id="cse-child-queued",
        execution_id="exec-cse-child",
        message="nested under cse while lease held",
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    child_admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=child_req.dispatch_id,
        thread_id=child_req.thread_id,
        model_id="cursor/composer-2.5",
    )
    ledger.admit(
        req=child_req,
        fingerprint=ledger.fingerprint(child_req),
        execution_id=child_req.execution_id,
        caller_agent="cursor-auto",
        resolved_model="cursor/composer-2.5",
        admission=child_admission,
        source_repo=lease_key,
        lease_key=lease_key,
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, nest_under FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (child_req.dispatch_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert row["nest_under"] == format_nest_under_cse("cse_nest1")


def test_dormant_cse_valid_nest_parent(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_CSE_URL, registration_id="reg-d")
        from services.git_integration_worker.cse_session_holders import (
            transition_seat_state,
        )

        transition_seat_state(conn, "cse_nest1", to_state="dormant")
        conn.commit()
    req = CursorDispatchRequest(
        thread_id="11669",
        model="cursor/composer-2.5",
        dispatch_id="child-dormant",
        execution_id="exec-d",
        message="nested under dormant",
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="cursor/composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-auto",
        resolved_model="cursor/composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
        nest_under=format_nest_under_cse("cse_nest1"),
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, nest_under FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "admitted"
    assert row["nest_under"] == format_nest_under_cse("cse_nest1")


