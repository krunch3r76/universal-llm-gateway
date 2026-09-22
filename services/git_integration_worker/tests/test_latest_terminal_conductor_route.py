"""GET /api/v1/cursor/dispatch/latest-terminal-conductor (read-only ledger projection)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.app import create_app
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return TestClient(create_app())


def _admit_terminal_conductor(
    ledger: CursorDispatchLedger, *, thread_id: str, dispatch_id: str, hop_seq: int
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="conductor",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:fixture",
        source_ref="todo:fixture",
        hop_seq=hop_seq,
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "closeout_body": "stop: CONSULT_PENDING\nexecution_id: exec-x\npoll_hint: wait",
            "closeout_turn": 3,
            "scoreboard_uri": "cortex://notes/system/scoreboards/fixture.md",
            "hop_seq": hop_seq,
        },
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")


def test_latest_terminal_conductor_returns_highest_hop(client: TestClient) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_terminal_conductor(ledger, thread_id="12291", dispatch_id="hop-1", hop_seq=1)
    _admit_terminal_conductor(ledger, thread_id="12291", dispatch_id="hop-2", hop_seq=2)

    resp = client.get(
        "/api/v1/cursor/dispatch/latest-terminal-conductor",
        params={"thread_id": "12291"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["dispatch_id"] == "hop-2"
    assert body["hop_seq"] == 2
    assert "closeout_body" in body
    assert "consult_pending_continue_owed" in body


def test_latest_terminal_conductor_404_when_missing(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/cursor/dispatch/latest-terminal-conductor",
        params={"thread_id": "99999"},
    )
    assert resp.status_code == 404
    body = resp.json()
    detail = body.get("detail", body)
    assert detail["found"] is False
