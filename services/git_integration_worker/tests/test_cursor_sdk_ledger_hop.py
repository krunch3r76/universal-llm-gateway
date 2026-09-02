"""R2: ledger record_json hop authority keys (todo:conductor-hop-reactor)."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    HOP_REASONS,
    hop_fields_from_record_json,
    merge_hop_patch,
    stamp_hop_on_record_json,
    validate_hop_reason,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-hop",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-hop",
        "execution_id": "exec-disp-hop",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    **hop_kwargs: object,
) -> None:
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        **hop_kwargs,
    )


def test_hop_reasons_reexport_matches_events_vocabulary() -> None:
    assert HOP_REASONS == frozenset({"spawn", "planned", "crash", "silent", "watchdog"})
    assert validate_hop_reason("spawn") is True
    assert validate_hop_reason("invalid") is False


def test_stamp_and_read_round_trip_all_six_keys() -> None:
    base = json.dumps({"lane": "A", "contract": "implement"}, sort_keys=True, separators=(",", ":"))
    stamped = stamp_hop_on_record_json(
        base,
        hop_seq=2,
        hop_from="pred-disp",
        hop_reason="spawn",
        hop_declared=True,
        hop_successor="succ-disp",
        hop_admit_error={"error": "timeout", "status_code": 503},
    )
    fields = hop_fields_from_record_json(stamped)
    assert fields == {
        "hop_seq": 2,
        "hop_from": "pred-disp",
        "hop_reason": "spawn",
        "hop_declared": True,
        "hop_successor": "succ-disp",
        "hop_admit_error": {"error": "timeout", "status_code": 503},
    }


def test_merge_hop_patch_preserves_non_hop_keys() -> None:
    base = json.dumps(
        {"lane": "A", "concurrency_posture": "sole_a"},
        sort_keys=True,
        separators=(",", ":"),
    )
    merged = merge_hop_patch(
        base,
        {
            "hop_successor": "succ-1",
            "hop_admit_error": "admit failed",
        },
    )
    data = json.loads(merged)
    assert data["lane"] == "A"
    assert data["concurrency_posture"] == "sole_a"
    assert data["hop_successor"] == "succ-1"
    assert data["hop_admit_error"] == "admit failed"


def test_invalid_hop_reason_raises_on_stamp() -> None:
    with pytest.raises(ValueError, match="hop_reason"):
        stamp_hop_on_record_json(
            "{}",
            hop_seq=1,
            hop_from="pred",
            hop_reason="not-a-reason",
        )


def test_invalid_hop_reason_raises_on_merge() -> None:
    with pytest.raises(ValueError, match="hop_reason"):
        merge_hop_patch("{}", {"hop_reason": "bogus"})


def test_admit_with_hop_kwargs_persists_keys_on_ledger_row() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="hop-successor", execution_id="exec-hop-successor")
    _admit(
        ledger,
        req,
        hop_seq=1,
        hop_from="pred-9964",
        hop_reason="planned",
        hop_declared=False,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("hop-successor",),
        ).fetchone()
    assert row is not None
    data = json.loads(row["record_json"])
    assert data["hop_seq"] == 1
    assert data["hop_from"] == "pred-9964"
    assert data["hop_reason"] == "planned"
    assert data["hop_declared"] is False
    assert "hop_successor" not in data
    assert "hop_admit_error" not in data


def test_admit_without_hop_kwargs_leaves_record_json_without_hop_keys() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="plain-admit", execution_id="exec-plain-admit")
    _admit(ledger, req)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("plain-admit",),
        ).fetchone()
    assert row is not None
    data = json.loads(row["record_json"])
    assert hop_fields_from_record_json(row["record_json"]) == {}
    assert "hop_seq" not in data


def test_admit_partial_hop_kwargs_raises() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="partial-hop", execution_id="exec-partial-hop")
    with pytest.raises(ValueError, match="hop_seq, hop_from, and hop_reason"):
        _admit(ledger, req, hop_seq=1, hop_from="pred")
