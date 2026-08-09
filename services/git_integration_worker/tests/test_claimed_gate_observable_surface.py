"""Observer-side claimed-gate phase distinctions (arc 6930 / inv-16).

These tests assert only on the persisted observer view — the same shape
exposed by ``GET /job-state`` and ``agent_bus_read.thread_get`` —
so wedged-pre-admit, wedged-post-admit-pre-bind, and healthy
answer+escalation remain distinguishable without reading writer internals.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_lifecycle import (
    PHASE_ADMITTED,
    PHASE_CLAIMED_PRE_ADMIT,
    PHASE_TERMINAL_DONE,
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


def _enqueue(
    *,
    thread_id: str = "7052",
    turn: int = 1,
    contract: str = "answer",
    escalation: str | None = "cdp/fable",
):
    return get_queue().enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract=contract,
        escalation=escalation,
    )


def test_observer_distinguishes_wedged_pre_admit_from_post_admit_and_answer_cdp():
    """Read-surface-only: three states must not collapse into silence."""
    queue = get_queue()
    ledger = get_ledger()

    pre = _enqueue(turn=1)
    assert queue.claim_next().job_id == pre.job_id
    pre_view = ledger.observer_state(job_id=pre.job_id)
    assert pre_view is not None
    assert pre_view["lifecycle_phase"] == PHASE_CLAIMED_PRE_ADMIT
    assert pre_view["admitted_at"] is None
    assert pre_view["bound_at"] is None
    assert pre_view["dispatch_id"] is None
    assert pre_view["status"] == "claimed"

    post = _enqueue(turn=2)
    assert queue.claim_next().job_id == post.job_id
    ledger.mark_admitted(post.job_id)
    post_view = ledger.observer_state(job_id=post.job_id)
    assert post_view is not None
    assert post_view["lifecycle_phase"] == PHASE_ADMITTED
    assert post_view["admitted_at"] is not None
    assert post_view["bound_at"] is None
    assert post_view["dispatch_id"] is None
    assert post_view["status"] == "claimed"

    healthy = _enqueue(turn=3, contract="answer", escalation="cdp/opus-5")
    assert queue.claim_next().job_id == healthy.job_id
    ledger.mark_admitted(healthy.job_id)
    ledger.mark_terminal(healthy.job_id, status="done", terminal_reason=None)
    done_view = ledger.observer_state(
        job_id=healthy.job_id, include_terminal=True
    )
    assert done_view is not None
    assert done_view["lifecycle_phase"] == PHASE_TERMINAL_DONE
    assert done_view["admitted_at"] is not None
    assert done_view["dispatch_id"] is None
    assert done_view["escalation"] == "cdp/opus-5"
    assert done_view["contract"] == "answer"
    assert done_view["status"] == "done"

    # Observer-only discrimination: phase+clocks alone separate all three.
    assert pre_view["lifecycle_phase"] != post_view["lifecycle_phase"]
    assert post_view["lifecycle_phase"] != done_view["lifecycle_phase"]
    assert pre_view["admitted_at"] is None and post_view["admitted_at"] is not None
    assert done_view["dispatch_id"] is None and done_view["escalation"]


def test_same_thread_claimed_uses_persisted_ledger_source():
    queue = get_queue()
    ledger = get_ledger()
    old = _enqueue(turn=1)
    assert queue.claim_next().job_id == old.job_id
    ledger.mark_admitted(old.job_id)
    new = _enqueue(turn=2)

    from_queue = queue.thread_lane_counts("7052", exclude_job_id=new.job_id)
    from_ledger = ledger.thread_lane_counts("7052", exclude_job_id=new.job_id)
    assert from_queue == from_ledger
    assert from_ledger["same_thread_claimed"] == 1
    assert from_ledger["same_thread_pending"] == 0


def test_job_state_http_returns_observer_view_for_thread():
    get_registry().register("7052-job-state-handler")
    client = TestClient(create_app())
    queue = get_queue()
    job = _enqueue(thread_id="7052-http", turn=1)
    assert queue.claim_next().job_id == job.job_id
    get_ledger().mark_admitted(job.job_id)

    resp = client.get(
        "/api/v1/git/cursor-auto/job-state",
        params={"thread_id": "7052-http"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["found"] is True
    job_view = body["job"]
    assert job_view["lifecycle_phase"] == PHASE_ADMITTED
    assert job_view["admitted_at"] is not None
    assert job_view["dispatch_id"] is None
    assert job_view["thread_id"] == "7052-http"


def test_bind_dispatch_stamps_bound_phase_and_clock():
    queue = get_queue()
    ledger = get_ledger()
    job = _enqueue(turn=1, contract="implement", escalation=None)
    assert queue.claim_next().job_id == job.job_id
    ledger.mark_admitted(job.job_id)
    ledger.bind_dispatch(job.job_id, dispatch_id="auto-testdispatch")
    view = ledger.observer_state(job_id=job.job_id)
    assert view is not None
    assert view["lifecycle_phase"] == "bound"
    assert view["bound_at"] is not None
    assert view["dispatch_id"] == "auto-testdispatch"
