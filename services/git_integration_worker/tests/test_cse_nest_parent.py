"""Nest resolution for ``cse:`` typed parents."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_bundles.holder_strings import format_nest_under_cse

from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    upsert_holder,
)
from services.git_integration_worker.cursor_auto.gate_serialize import (
    derive_cse_nest_under,
)
from services.git_integration_worker.cursor_auto.nest_parent import resolve_nest_under
from services.git_integration_worker.cursor_auto.queue import AutoJob
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


def _job(*, thread_id: str = "11667") -> AutoJob:
    return AutoJob(
        job_id="job-nest",
        thread_id=thread_id,
        turn_number=1,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE\nscope: foo\n",
        from_agent="cdp-operator-11667-mailbox",
        to_agent="cursor-auto",
        desired_model="cursor/composer-2.5",
        desired_effort="high",
        contract="implement",
    )


@pytest.mark.asyncio
async def test_resolve_nest_under_auto_derives_cse_parent(
    ledger: CursorDispatchLedger,
) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-n",
            lane_thread_id="11667",
        )
        conn.commit()
    gate_plan = {"action": "nest_park", "reason": "gate_at_capacity_prefer_park"}
    client = SimpleNamespace()
    queue = SimpleNamespace()
    result = await resolve_nest_under(
        _job(),
        client=client,
        queue=queue,
        gate_plan=gate_plan,
        work_bounded=True,
        contract="implement",
    )
    assert result == format_nest_under_cse("cse_nest1")


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


def test_derive_cse_nest_under_from_lane(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_CSE_URL,
            lane_thread_id="11667",
        )
        conn.commit()
    assert derive_cse_nest_under(_job()) == format_nest_under_cse("cse_nest1")
