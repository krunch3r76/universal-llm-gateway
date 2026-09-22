"""D3 consult_pending_continue_owed + fire_consult_pending_continue."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from libs.claude_bundles.conductor_stop import is_consult_pending_wait
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    consult_pending_continue_owed,
    fire_consult_pending_continue,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:operator-ear-by-token-fixture"
_CLOSEOUT = (
    Path(__file__).resolve().parent / "fixtures/operator_ear/12291_turn3_closeout.txt"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "12291",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-consult-1",
        "execution_id": "exec-pred-consult-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_conductor(ledger: CursorDispatchLedger, req: CursorDispatchRequest) -> None:
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
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=1,
    )


def test_real_closeout_body_is_consult_pending_wait() -> None:
    assert is_consult_pending_wait(_CLOSEOUT)


def test_consult_pending_continue_owed_true_with_reply_fn() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_body": _CLOSEOUT,
            "closeout_turn": 3,
            "closeout_stop_tokens": ["CONSULT_PENDING"],
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    row = {"dispatch_id": req.dispatch_id, "thread_id": "12291", "status": "completed", "record_json": json.dumps({"closeout_turn": 3, "closeout_body": _CLOSEOUT})}
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    row = {k: raw[k] for k in raw.keys()}
    assert consult_pending_continue_owed(row, reply_fn=lambda *_: True)


def test_consult_pending_continue_owed_false_without_reply() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="pred-consult-2")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_body": _CLOSEOUT, "closeout_turn": 3},
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    row = {k: raw[k] for k in raw.keys()}
    assert not consult_pending_continue_owed(row, reply_fn=lambda *_: False)


@pytest.mark.asyncio
async def test_fire_consult_pending_continue_stamps_key() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="pred-consult-3")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_body": _CLOSEOUT, "closeout_turn": 3, "hop_seq": 1},
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    row = {k: raw[k] for k in raw.keys()}
    hop_body = {"hop_seq": 2, "dispatch_thread_id": "12291", "hop_reason": "consult_harvest"}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.build_hop_team_dispatch_body",
        return_value=hop_body,
    ), patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "succ-consult-1"})),
    ):
        ok = await fire_consult_pending_continue(row)
    assert ok is True
    with ledger._connect() as conn:
        raw2 = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    data = json.loads(raw2[0])
    assert "hop_consult_harvest_continued_at" in data
