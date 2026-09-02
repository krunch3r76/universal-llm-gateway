"""Hop triplet on CursorDispatchRequest and admit wire (todo:conductor-hop-reactor R4)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import hop_fields_from_record_json

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def test_cursor_dispatch_request_accepts_hop_triplet() -> None:
    req = CursorDispatchRequest(
        thread_id="9964",
        model="cursor/composer-2.5",
        dispatch_id="succ-hop-2",
        execution_id="exec-succ-hop-2",
        message="conductor hop",
        hop_seq=2,
        hop_from="pred-hop-1",
        hop_reason="planned",
    )
    assert req.hop_seq == 2
    assert req.hop_from == "pred-hop-1"
    assert req.hop_reason == "planned"


def test_cursor_dispatch_request_rejects_partial_hop_triplet() -> None:
    with pytest.raises(ValueError, match="hop_seq, hop_from, and hop_reason"):
        CursorDispatchRequest(
            thread_id="9964",
            model="cursor/composer-2.5",
            dispatch_id="succ-hop-2",
            execution_id="exec-succ-hop-2",
            message="conductor hop",
            hop_seq=2,
            hop_from="pred-hop-1",
        )


def test_admit_stamps_hop_fields_from_request() -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="9964",
        model="cursor/composer-2.5",
        dispatch_id="succ-hop-3",
        execution_id="exec-succ-hop-3",
        message="conductor hop",
        hop_seq=2,
        hop_from="pred-hop-1",
        hop_reason="planned",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="conductor-hop",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="light-bounded",
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:conductor-hop-fixture",
        source_ref="todo:conductor-hop-fixture",
        hop_seq=2,
        hop_from="pred-hop-1",
        hop_reason="planned",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields["hop_seq"] == 2
    assert fields["hop_from"] == "pred-hop-1"
    assert fields["hop_reason"] == "planned"
