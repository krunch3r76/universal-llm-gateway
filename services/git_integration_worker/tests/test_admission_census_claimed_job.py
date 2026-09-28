"""Job-keyed admission census: claimed Auto job + same-thread supersede."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from claude_bundles.request_admission_census import census_match_ids
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto import supersede as auto_supersede
from services.git_integration_worker.cursor_auto.admission_census import (
    attach_claimed_job_from_ledger,
)
from services.git_integration_worker.cursor_auto.directive import build_sdk_message
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.supersede import (
    compose_supersede_preamble,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
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


def test_same_thread_request_supersedes_claimed_job(
    cursor_auto_client: TestClient, tmp_path
):
    """Claimed predecessor with a live CancelRun handle — run_cancel + SUPERSEDE NOTICE."""
    get_registry().register("admission-census-handler")
    queue = get_queue()
    old = _enqueue(thread_id="13081", turn=1)
    assert queue.claim_next().job_id == old.job_id
    get_ledger().mark_admitted(old.job_id)

    void_dispatch_id = "auto-admission-census-13081"
    run = MagicMock()
    register_live_run(
        dispatch_id=void_dispatch_id,
        thread_id="13081",
        source_repo=str(tmp_path),
        run=run,
    )
    try:
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
        superseded = body["superseded"]
        assert superseded is not None
        assert superseded["superseded_job_id"] == old.job_id
        assert superseded["method"] == "run_cancel"
        assert superseded["superseded_dispatch_id"] == void_dispatch_id
        run.cancel.assert_called_once()

        new_job = queue.get(body["job_id"])
        pending = auto_supersede._PENDING.get(new_job.job_id)
        assert pending is not None
        settlement = {"mark": pending.mark, "revert": None}
        directive_body = build_sdk_message(
            new_job.body, contract="implement", lane="B"
        )
        message = f"{compose_supersede_preamble(settlement)}\n\n{directive_body}"
        assert message.startswith("=== SUPERSEDE NOTICE (substrate-generated) ===")
        assert void_dispatch_id in message
    finally:
        unregister_live_run(dispatch_id=void_dispatch_id)
        auto_supersede._PENDING.clear()


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return TestClient(create_app())
