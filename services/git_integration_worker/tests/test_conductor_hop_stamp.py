"""H2 lineage stamp — derive + predecessor hop_successor in one transaction."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_conductor_hop_stamp import (
    HOP_ADMITTED_BY_KEY,
    HOP_LINEAGE_CLAIM_MISMATCH_KEY,
    apply_hop_lineage_stamp,
    derive_hop_admitted_by,
    derive_hop_lineage,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:hop-stamp-fixture"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-stamp-1",
        "execution_id": "exec-pred-stamp-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_conductor(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    caller_agent: str = "cursor",
    hop_seq: int = 1,
    hop_from: str | None = None,
    terminal: bool = True,
) -> None:
    admit_kwargs: dict = {
        "req": req,
        "fingerprint": ledger.fingerprint(req),
        "execution_id": req.execution_id,
        "caller_agent": caller_agent,
        "resolved_model": "composer-2.5",
        "admission": CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        "contract": "conductor",
        "source_repo": "/repo",
        "lease_key": "/repo",
        "work_key": _WORK_KEY,
        "source_ref": _WORK_KEY,
    }
    if hop_seq == 1:
        admit_kwargs.update(
            hop_seq=1,
            hop_from=hop_from or "spawn-parent",
            hop_reason="spawn",
        )
    ledger.admit(**admit_kwargs)
    if terminal:
        ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")


def test_derive_hop_admitted_by_liaison_vs_watchdog() -> None:
    assert derive_hop_admitted_by(caller_agent="cursor", hop_reason="planned") == "liaison"
    assert (
        derive_hop_admitted_by(caller_agent="conductor-hop", hop_reason="watchdog")
        == "watchdog"
    )


def test_lineage_stamp_sets_predecessor_successor() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req(dispatch_id="pred-stamp-1"), hop_seq=1)
    succ_req = _req(dispatch_id="succ-stamp-2")
    ledger.admit(
        req=succ_req,
        fingerprint=ledger.fingerprint(succ_req),
        execution_id=succ_req.execution_id,
        caller_agent="liaison",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=succ_req.dispatch_id,
            thread_id=succ_req.thread_id,
            model_id="composer-2.5",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=2,
        hop_from="pred-stamp-1",
        hop_reason="planned",
    )
    with ledger._connect() as conn:
        pred = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-stamp-1'"
        ).fetchone()
        succ = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='succ-stamp-2'"
        ).fetchone()
    pred_data = json.loads(pred["record_json"])
    succ_data = json.loads(succ["record_json"])
    assert pred_data.get("hop_successor") == "succ-stamp-2"
    assert succ_data.get("hop_from") == "pred-stamp-1"
    assert succ_data.get("hop_seq") == 2
    assert succ_data.get(HOP_ADMITTED_BY_KEY) == "liaison"


def test_lineage_records_body_triplet_mismatch() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req(dispatch_id="pred-stamp-1"), hop_seq=1)
    with ledger._connect() as conn:
        lineage = derive_hop_lineage(
            conn,
            thread_id="9964",
            work_key=_WORK_KEY,
            caller_agent="liaison",
            body_triplet={
                "hop_from": "wrong-id",
                "hop_seq": 2,
                "hop_reason": "planned",
            },
        )
        assert lineage is not None
        assert lineage.hop_from == "pred-stamp-1"
        assert lineage.claim_mismatch == (
            {"field": "hop_from", "claimed": "wrong-id", "derived": "pred-stamp-1"},
        )
        stamped = apply_hop_lineage_stamp(
            conn,
            incoming_dispatch_id="succ-stamp-2",
            thread_id="9964",
            record_json="{}",
            lineage=lineage,
        )
    data = json.loads(stamped)
    assert data[HOP_LINEAGE_CLAIM_MISMATCH_KEY] == [
        {"field": "hop_from", "claimed": "wrong-id", "derived": "pred-stamp-1"}
    ]


def test_list_mission_terminal_chain_orders_by_hop_seq() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        list_mission_terminal_chain,
    )

    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req(dispatch_id="hop-1"), hop_seq=1)
    _admit_conductor(ledger, _req(dispatch_id="hop-2"))
    _admit_conductor(ledger, _req(dispatch_id="hop-3"))
    chain = list_mission_terminal_chain(work_key=_WORK_KEY)
    assert [row["dispatch_id"] for row in chain] == ["hop-1", "hop-2", "hop-3"]
