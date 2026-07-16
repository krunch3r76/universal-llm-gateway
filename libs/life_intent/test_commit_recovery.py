"""Decisive falsifier — admitted-response-loss with real CursorDispatchLedger."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from life_intent.commit import WorkerAdmissionIndeterminateError, apply_commit
from life_intent.proposal_store import clear_store, create_proposal, get_proposal
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_FIX_INTENT = {
    "verb": "fix",
    "subject": "ledger falsifier",
    "detail": "Users see timeout after thirty seconds consistently.",
    "urgency": "normal",
}


@pytest.fixture(autouse=True)
def _reset() -> None:
    clear_store()


@pytest.fixture
def ledger_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CursorDispatchLedger:
    db_path = tmp_path / "cursor-sdk-dispatch.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    assert ledger._db_path == db_path or ledger._db_path.parent == tmp_path
    return ledger


def _handle_dict(*, dispatch_id: str, thread_id: str, execution_id: str) -> dict:
    return {
        "request_id": "reqfix",
        "execution_id": execution_id,
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "resolved_model": "composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "sdk",
        "to_agent": f"cursor-sdk:dispatch:{execution_id}",
        "handoff_contract": "light-bounded",
        "packet_path": "packet.md",
        "message": None,
        "caller_agent": "web-anthropic",
        "read_only": True,
        "aligned_knobs": None,
        "prompt_preamble": None,
        "thread_subject": "ledger falsifier",
        "pointer_body": "ptr",
        "effective_bus_lifecycle": "persistent",
        "parent_dispatch_thread_id": None,
        "dispatch_thread_id": None,
        "density_triage": None,
        "review_opt_out_reason_code": None,
        "auto_review_child": False,
        "auto_review_defaulted": False,
        "claimed_via_atomic": False,
        "admitted": True,
        "alignment_warnings": [],
        "knob_resolution": [],
    }


def test_admitted_response_loss_retry_one_ledger_row(
    monkeypatch: pytest.MonkeyPatch, ledger_db: CursorDispatchLedger
) -> None:
    """Worker admits once; caller loses response; retry reuses identity."""
    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    monkeypatch.setattr(
        "life_intent.commit._write_packet", lambda *_a, **_k: "packet.md"
    )
    monkeypatch.setattr(
        "life_intent.commit._ensure_entity",
        lambda _seed: "todo:life-intent-ledger-falsifier",
    )
    monkeypatch.setattr("life_intent.commit._create_context_edge", lambda *_a: None)

    dispatch_id = "reqfix-deadbeef"
    execution_id = "exec-deadbeef"
    thread_id = "5125"
    handle_data = _handle_dict(
        dispatch_id=dispatch_id, thread_id=thread_id, execution_id=execution_id
    )

    class _Handle:
        def __init__(self, data: dict) -> None:
            for k, v in data.items():
                setattr(self, k, v)

    prepared = _Handle(handle_data)
    task_spawns = {"n": 0}
    submits = {"n": 0}

    async def _prepare(**_kwargs: object) -> _Handle:
        return prepared

    async def _submit(h: object) -> str:
        submits["n"] += 1
        req = CursorDispatchRequest(
            thread_id=thread_id,
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=execution_id,
            packet_path="packet.md",
            read_only=True,
        )
        fp = CursorDispatchLedger.fingerprint(req)
        admission = CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        )
        cached = ledger_db.admit(
            req=req,
            fingerprint=fp,
            execution_id=execution_id,
            caller_agent="web-anthropic",
            resolved_model="composer-2.5",
            admission=admission,
            read_only=True,
        )
        if cached is None:
            task_spawns["n"] += 1
        if submits["n"] == 1:
            raise WorkerAdmissionIndeterminateError("response lost after admit")
        return thread_id

    monkeypatch.setattr("life_intent.commit._prepare_recon_handle", _prepare)
    monkeypatch.setattr("life_intent.commit._submit_prepared_handle", _submit)
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_to_dict",
        lambda h: {k: getattr(h, k) for k in handle_data},
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_from_dict",
        lambda data: _Handle(data),
    )

    proposal_id = create_proposal(
        normalized_intent=_FIX_INTENT,
        work_order="work",
        verb="fix",
        lane="bug_recon",
    )

    first = asyncio.run(apply_commit(proposal_id))
    assert first.code == "commit_indeterminate"
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.status == "indeterminate"
    assert row.dispatch_handle is not None
    assert row.dispatch_handle["dispatch_id"] == dispatch_id
    assert row.dispatch_ref is None

    second = asyncio.run(apply_commit(proposal_id))
    assert second.committed is True
    assert second.recon_ref == thread_id

    with ledger_db._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchall()
    assert len(rows) == 1
    assert task_spawns["n"] == 1
    assert submits["n"] == 2
    final = get_proposal(proposal_id)
    assert final is not None
    assert final.status == "completed"
    assert final.dispatch_handle["dispatch_id"] == dispatch_id
    assert final.dispatch_handle["execution_id"] == execution_id
    assert final.dispatch_handle["thread_id"] == thread_id
