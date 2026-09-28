"""Job-keyed admission census: claimed Auto job + same-thread supersede."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from claude_bundles.request_admission_census import census_match_ids
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.admission_census import (
    attach_claimed_job_from_ledger,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_auto_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _enqueue(*, thread_id: str, turn: int):
    return get_queue().enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_census_match_ids_includes_claimed_job_row_from_ledger():
    """AC1: identity_rows omitted claimed jobs — census_match_ids only saw CSE rows."""
    queue = get_queue()
    old = _enqueue(thread_id="13075", turn=1)
    assert queue.claim_next().job_id == old.job_id
    get_ledger().mark_admitted(old.job_id)

    snap = attach_claimed_job_from_ledger({"rows": []}, thread_id="13075")
    assert census_match_ids("13075", snap) == ["cursor-auto-job:" + old.job_id]


def test_same_thread_request_supersedes_claimed_job(cursor_auto_client: TestClient):
    get_registry().register("admission-census-handler")
    queue = get_queue()
    old = _enqueue(thread_id="13081", turn=1)
    assert queue.claim_next().job_id == old.job_id
    get_ledger().mark_admitted(old.job_id)

    resp = cursor_auto_client.post(
        "/api/v1/git/cursor-auto/enqueue",
        json={
            "thread_id": "13081",
            "turn_number": 2,
            "subject": "turn 2",
            "body": "TYPE: DIRECTIVE\n",
            "from_agent": "web-anthropic",
            "to_agent": "cursor",
            "desired_model": "auto",
            "desired_effort": "medium",
            "contract": "implement",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["superseded"] is not None
    assert body["superseded"]["superseded_job_id"] == old.job_id


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return TestClient(create_app())
