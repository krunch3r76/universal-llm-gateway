"""API-role dispatch ledger POST + work-key projection."""

from __future__ import annotations

import json

import pytest
from event_store.model_rate_table import ModelRateRow
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_dispatch_read import (
    work_key_ledger_projection,
)
from services.git_integration_worker.cursor_sdk_work_claim import occupancy
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes.cursor_claim import router as claim_router

_KEY = "todo:api-role-ledger-test"
_ADMIT_CLOSE_KEY = "todo:api-role-admit-close-test"
_EXEC_UUID = "baf3afd7-dc49-472b-9fb0-709a88d9d625"


@pytest.fixture
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


@pytest.mark.offline
def test_upsert_api_role_completes_admitted_row_without_twin(
    ledger: CursorDispatchLedger,
) -> None:
    """Admitted API-role row (dispatch_id=execution uuid) closes in place.

    Breaks when completion inserts api-{uuid} while admit row stays admitted:
    work_key occupancy stays acting until manual discard.
    """
    req = CursorDispatchRequest(
        thread_id="12286",
        model="anthropic/claude-sonnet-4-6",
        dispatch_id=_EXEC_UUID,
        execution_id=_EXEC_UUID,
        message="skeptic slice",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=_EXEC_UUID,
        caller_agent="maestro",
        resolved_model="anthropic/claude-sonnet-4-6",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=_EXEC_UUID,
            thread_id=req.thread_id,
            model_id="anthropic/claude-sonnet-4-6",
        ),
        contract="freeform",
        work_key=_ADMIT_CLOSE_KEY,
        identity_class="declared",
    )
    assert occupancy(_ADMIT_CLOSE_KEY, ledger=ledger)["state"] == "acting"

    returned = ledger.upsert_api_role_completed_dispatch(
        execution_id=_EXEC_UUID,
        work_key=_ADMIT_CLOSE_KEY,
        resolved_model="anthropic/claude-sonnet-4-6",
        contract="freeform",
        record_json='{"substrate":"api_role"}',
        stamp="2026-11-02T01:00:00+00:00",
    )
    assert returned == _EXEC_UUID

    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=? OR execution_id=? OR dispatch_id=?",
            (_EXEC_UUID, _EXEC_UUID, f"api-{_EXEC_UUID}"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["dispatch_id"] == _EXEC_UUID
    assert rows[0]["status"] == "completed"
    assert occupancy(_ADMIT_CLOSE_KEY, ledger=ledger)["state"] == "idle"


def _admit_api_role(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    execution_id: str,
    work_key: str,
    message: str = "skeptic slice",
    thread_id: str = "12286",
) -> None:
    """Admit one API-role row the way ``team_dispatch(op=generate)`` does."""
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="anthropic/claude-sonnet-4-6",
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        message=message,
        nest_under="conductor-1",
        wake_lane="maestro",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=execution_id,
        caller_agent="maestro",
        resolved_model="anthropic/claude-sonnet-4-6",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="anthropic/claude-sonnet-4-6",
        ),
        contract="implement",
        work_key=work_key,
        identity_class="declared",
    )


def _complete(
    ledger: CursorDispatchLedger,
    *,
    execution_id: str,
    work_key: str,
    contract: str | None = None,
    stamp: str = "2026-11-02T01:00:00+00:00",
) -> str:
    return ledger.upsert_api_role_completed_dispatch(
        execution_id=execution_id,
        work_key=work_key,
        resolved_model="anthropic/claude-sonnet-4-6",
        contract=contract,
        record_json=(
            '{"substrate":"api_role","usage":{"input_tokens":50},'
            '"usage_capture_status":"captured"}'
        ),
        stamp=stamp,
    )


def _row(ledger: CursorDispatchLedger, dispatch_id: str) -> dict:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


@pytest.mark.offline
def test_close_in_place_preserves_admit_record_json_and_fields(
    ledger: CursorDispatchLedger,
) -> None:
    """F1/F6: completion merges onto the admit row, it does not replace it.

    Breaks when the completion assigns ``record_json``/``contract``/``work_key``
    wholesale: nest_under, wake_lane and the admitted contract are erased, so
    parent wake routing and the conductor nest are lost at closeout.
    """
    exec_id = "0ad1b5be-0000-4000-8000-00000000f1f6"
    key = "todo:api-role-record-preserve"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)
    admitted = _row(ledger, exec_id)

    returned = _complete(ledger, execution_id=exec_id, work_key="todo:other-key")
    assert returned == exec_id

    closed = _row(ledger, exec_id)
    assert closed["contract"] == "implement"
    assert closed["work_key"] == key
    assert closed["wake_lane"] == admitted["wake_lane"]
    before = json.loads(admitted["record_json"] or "{}")
    after = json.loads(closed["record_json"] or "{}")
    for field, value in before.items():
        assert after[field] == value, field
    assert after["substrate"] == "api_role"
    assert after["usage"] == {"input_tokens": 50}


@pytest.mark.offline
def test_close_in_place_runs_mark_terminal_side_effects(
    ledger: CursorDispatchLedger,
) -> None:
    """F3: the in-place close fires ``mark_terminal``'s side effects.

    Breaks when the close is a bare UPDATE: no stop_service row is enqueued, so
    the SDK process for a completed dispatch is never asked to stop.
    """
    exec_id = "0ad1b5be-0000-4000-8000-00000000f300"
    key = "todo:api-role-side-effects"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)

    assert _complete(ledger, execution_id=exec_id, work_key=key) == exec_id

    row = _row(ledger, exec_id)
    assert row["status"] == "completed"
    assert row["terminal_status"] == "completed"
    assert row["terminal_at"] is not None
    with ledger._connect() as conn:
        stop = conn.execute(
            "SELECT stop_id FROM cursor_dispatch_stop_service WHERE stop_id=?",
            (exec_id,),
        ).fetchone()
    assert stop is not None


@pytest.mark.offline
def test_already_terminal_row_is_not_flipped_to_completed(
    ledger: CursorDispatchLedger,
) -> None:
    """F2: a row a concurrent cancel already closed stays cancelled.

    Breaks when the status predicate lives only in the SELECT: a cancel that
    commits between the SELECT and the UPDATE is overwritten with completed, and
    the cancelled dispatch is reported as a successful one.
    """
    exec_id = "0ad1b5be-0000-4000-8000-00000000f200"
    key = "todo:api-role-already-terminal"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled' WHERE dispatch_id=?",
            (exec_id,),
        )
        conn.commit()

    returned = _complete(ledger, execution_id=exec_id, work_key=key)

    row = _row(ledger, returned)
    assert row["status"] == "cancelled"
    assert row["terminal_status"] == "cancelled"
    with ledger._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=? OR execution_id=?",
            (f"api-{exec_id}", exec_id),
        ).fetchone()["n"]
    assert count == 1


@pytest.mark.offline
def test_parked_sibling_with_same_execution_id_is_not_matched(
    ledger: CursorDispatchLedger,
) -> None:
    """F4: a parked_waiting sibling sharing the execution_id is not closed.

    Breaks on ``status NOT IN (terminal)`` + ``ORDER BY rowid DESC``: the newest
    row wins, so ``park_for_restart``'s inherited execution_id makes the
    completion close the parked row instead of the dispatch that ran.
    """
    exec_id = "0ad1b5be-0000-4000-8000-00000000f400"
    key = "todo:api-role-parked-sibling"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)
    _admit_api_role(
        ledger,
        dispatch_id="parked-sibling-1",
        execution_id=exec_id,
        work_key=f"{key}-sibling",
        message="parked twin",
        thread_id="12287",
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='parked_waiting' "
            "WHERE dispatch_id='parked-sibling-1'",
        )
        conn.commit()

    assert _complete(ledger, execution_id=exec_id, work_key=key) == exec_id

    assert _row(ledger, "parked-sibling-1")["status"] == "parked_waiting"
    assert _row(ledger, exec_id)["status"] == "completed"


@pytest.mark.offline
def test_second_completion_is_idempotent_no_twin_no_double_usage(
    ledger: CursorDispatchLedger,
) -> None:
    """F5: a repeated completion POST does not insert an ``api-`` twin.

    Breaks when the second call falls through to the synthetic insert: the
    work_key projection then sums usage across two rows and the dispatch is
    billed twice.
    """
    exec_id = "0ad1b5be-0000-4000-8000-00000000f500"
    key = "todo:api-role-idempotent"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)

    first = _complete(ledger, execution_id=exec_id, work_key=key)
    second = _complete(
        ledger, execution_id=exec_id, work_key=key, stamp="2026-11-02T02:00:00+00:00"
    )
    assert first == second == exec_id

    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=? OR execution_id=?",
            (f"api-{exec_id}", exec_id),
        ).fetchall()
    assert [r["dispatch_id"] for r in rows] == [exec_id]
    payload = work_key_ledger_projection(key, ledger=ledger, limit=5)
    assert payload["total_dispatches"] == 1


@pytest.mark.offline
def test_route_returns_the_dispatch_id_that_was_closed(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: the POST response names the row that actually carries the completion.

    Breaks when the route returns ``api-{execution_id}`` by construction: the
    caller reconciles economics against a dispatch_id with no ledger row.
    """
    monkeypatch.setattr(
        "services.git_integration_worker.api_role_dispatch_ledger.CursorDispatchLedger.instance",
        lambda: ledger,
    )
    exec_id = "0ad1b5be-0000-4000-8000-00000000f700"
    key = "todo:api-role-route-id"
    _admit_api_role(ledger, dispatch_id=exec_id, execution_id=exec_id, work_key=key)

    app = FastAPI()
    app.include_router(claim_router)
    resp = TestClient(app).post(
        "/api/v1/cursor/api-role-dispatch",
        json={
            "execution_id": exec_id,
            "work_key": key,
            "resolved_model": "anthropic/claude-sonnet-4-6",
            "contract": "freeform",
            "usage": {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["dispatch_id"] == exec_id
    payload = work_key_ledger_projection(key, ledger=ledger, limit=5)
    assert [r["dispatch_id"] for r in payload["dispatches"]] == [exec_id]
    assert payload["dispatches"][0]["usage_cost"]["input_tokens"] == 50


@pytest.mark.offline
def test_post_api_role_dispatch_records_usage(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.api_role_dispatch_ledger.CursorDispatchLedger.instance",
        lambda: ledger,
    )
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/cursor/api-role-dispatch",
        json={
            "execution_id": "abc123",
            "work_key": _KEY,
            "resolved_model": "anthropic/claude-sonnet-4-6",
            "contract": "freeform",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
            },
        },
    )
    assert resp.status_code == 200
    payload = work_key_ledger_projection(_KEY, ledger=ledger, limit=5)
    assert payload["total_dispatches"] == 1
    row = payload["dispatches"][0]
    assert row["dispatch_id"] == "api-abc123"
    assert row["work_key"] == _KEY
    assert row.get("usage_cost") is not None
    assert row["usage_cost"].get("input_tokens") == 50


@pytest.mark.offline
def test_post_api_role_dispatch_normalizes_outcome_usage(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outcome-shaped usage keys are normalized before ledger projection."""
    monkeypatch.setattr(
        "services.git_integration_worker.api_role_dispatch_ledger.CursorDispatchLedger.instance",
        lambda: ledger,
    )
    fixed_rate = ModelRateRow(
        model_id="anthropic/claude-haiku-5-5",
        input_rate_per_m=0.10,
        output_rate_per_m=0.50,
        source="manual_seed_local",
        updated_at="2026-01-01T00:00:00+00:00",
        cache_read_rate_per_m=0.01,
        cache_write_rate_per_m=0.125,
    )
    monkeypatch.setattr(
        "event_store.dispatch_economics_pricing.resolve_rate",
        lambda *_a, **_k: fixed_rate,
    )
    raw_usage = {
        "prompt_tokens": 500,
        "completion_tokens": 100,
        "cached_tokens": 200,
        "cache_write_tokens": 50,
        "total_tokens": 600,
    }
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/cursor/api-role-dispatch",
        json={
            "execution_id": "norm-outcome-1",
            "work_key": _KEY,
            "resolved_model": "anthropic/claude-haiku-5-5",
            "contract": "freeform",
            "usage": raw_usage,
        },
    )
    assert resp.status_code == 200
    payload = work_key_ledger_projection(_KEY, ledger=ledger, limit=5)
    row = next(
        r for r in payload["dispatches"] if r["dispatch_id"] == "api-norm-outcome-1"
    )
    usage_cost = row.get("usage_cost")
    assert usage_cost is not None
    assert usage_cost.get("input_tokens") == 500
    assert usage_cost.get("output_tokens") == 100
    assert usage_cost.get("cache_read_tokens") == 200
    assert usage_cost.get("cache_write_tokens") == 50
    assert usage_cost.get("total_tokens") == 850
    assert usage_cost.get("total_tokens_basis") == "exclusive_sum"
    assert usage_cost.get("cost_usd") == pytest.approx(0.00010825)


@pytest.mark.offline
def test_post_api_role_dispatch_accepts_null_usage(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.api_role_dispatch_ledger.CursorDispatchLedger.instance",
        lambda: ledger,
    )
    app = FastAPI()
    app.include_router(claim_router)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/cursor/api-role-dispatch",
        json={
            "execution_id": "no-usage-1",
            "work_key": _KEY,
            "resolved_model": "anthropic/claude-sonnet-4-6",
            "contract": "freeform",
            "usage": None,
        },
    )
    assert resp.status_code == 200
    payload = work_key_ledger_projection(_KEY, ledger=ledger, limit=5)
    row = next(r for r in payload["dispatches"] if r["dispatch_id"] == "api-no-usage-1")
    assert row.get("usage_cost") is None
