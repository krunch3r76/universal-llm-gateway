"""G3 AC13 cursor-auto half: job=answer is a legal admit on that surface.

Breaks when cursor-auto admit refuses handoff_contract=answer as job_unknown
before the rest of admission. Pairs with
systems/frontier_consult/test_answer_job_ac13.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "capture_wt_baseline_with_hashes",
        lambda *_a, **_k: {"admit_head": "deadbeef", "files": {}},
    )
    from services.git_integration_worker.app import create_app

    return TestClient(create_app())


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_cursor_auto_admit_accepts_job_answer(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json={
            "thread_id": "11767",
            "model": "cursor/composer-2.5",
            "dispatch_id": "auto-answer-ac13",
            "execution_id": "exec-answer-ac13",
            "message": "answer on the cursor-auto surface",
            "handoff_contract": "answer",
            "admitted_via": "cursor-auto",
        },
    )
    payload = resp.json()
    assert resp.status_code == 200, payload
    error = payload.get("error") or {}
    assert error.get("reason") != "job_unknown"
    assert error.get("event") != "dispatch.job.refused"
