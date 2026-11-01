"""Work registry auto-record and terminal work_status stamps."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_work_registry_admit import (
    record_dispatch_registry_best_effort,
    stamp_registry_terminal_work_status,
    upsert_frontier_registry_dispatch,
)

pytestmark = pytest.mark.offline


def test_upsert_frontier_row_for_orion_like_seat() -> None:
    ledger = CursorDispatchLedger.instance()
    rid = upsert_frontier_registry_dispatch(
        dispatch_id="exec-orion-1",
        thread_id="15851",
        caller_agent="orion",
        seat_name="orion",
        address={"kind": "api_role", "thread_id": "15851"},
        work_key="todo:orion-task",
        resolved_model="openai/gpt-5",
        contract="consult",
        ledger=ledger,
    )
    assert rid == "exec-orion-1"
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT caller_agent, record_json, status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("exec-orion-1",),
        ).fetchone()
    assert row["caller_agent"] == "orion"
    rec = json.loads(row["record_json"])
    assert rec["seat_name"] == "orion"
    assert rec["address"]["kind"] == "api_role"


def test_terminal_stamp_sets_landed_on_completed() -> None:
    ledger = CursorDispatchLedger.instance()
    record_dispatch_registry_best_effort(
        dispatch_id="disp-term-1",
        thread_id="T1",
        caller_agent="cursor-sdk",
        seat_name="cursor-sdk",
        address={"kind": "cursor_sdk", "dispatch_id": "disp-term-1", "worker_thread": "T1"},
        ledger=ledger,
    )
    ledger.mark_terminal(dispatch_id="disp-term-1", terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("disp-term-1",),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("work_status") == "landed"


def test_registry_record_route() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.git_integration_worker.routes.cursor_claim import router

    app = FastAPI()
    app.include_router(router)
    tc = TestClient(app)
    resp = tc.post(
        "/api/v1/cursor/registry/record",
        json={
            "dispatch_id": "disp-route-1",
            "thread_id": "21650",
            "caller_agent": "cursor-sdk",
            "seat_name": "cursor-sdk",
            "address": {
                "kind": "cursor_sdk",
                "dispatch_id": "disp-route-1",
                "worker_thread": "21650",
            },
            "work_key": "todo:route-record",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["registry_row_id"] == "disp-route-1"
    assert "_next" in body
