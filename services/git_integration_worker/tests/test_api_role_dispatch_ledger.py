"""API-role dispatch ledger POST + work-key projection."""

from __future__ import annotations

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
    row = next(
        r for r in payload["dispatches"] if r["dispatch_id"] == "api-no-usage-1"
    )
    assert row.get("usage_cost") is None
