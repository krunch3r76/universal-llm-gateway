"""API-role dispatch ledger POST + work-key projection."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_dispatch_read import work_key_ledger_projection
from services.git_integration_worker.routes.cursor_claim import router as claim_router

_KEY = "todo:api-role-ledger-test"


@pytest.fixture
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


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
