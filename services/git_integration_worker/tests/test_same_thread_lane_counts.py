"""Thread-scoped pending/claimed in the Auto enqueue admit response (arc 6885).

Alone vs backed-up lanes are operationally opposite; process-global
``queue.pending`` / ``queue.claimed`` cannot discriminate them. These tests
pin the discriminant the admit response must carry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import (
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)


def _enqueue(queue: AutoJobQueue, *, thread_id: str, turn_number: int):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="TYPE: DIRECTIVE\nvision: lane-count\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CURSOR_AUTO_HOP_WATCHES_PATH",
        str(tmp_path / "hop_cadence_watches.json"),
    )
    reset_queue_for_tests(durable=False)
    return TestClient(create_app())


def _post_enqueue(client: TestClient, *, thread_id: str, turn_number: int):
    return client.post(
        "/api/v1/git/cursor-auto/enqueue",
        json={
            "thread_id": thread_id,
            "turn_number": turn_number,
            "subject": f"turn {turn_number}",
            "body": "TYPE: DIRECTIVE\nvision: lane-count\n",
            "from_agent": "web-anthropic",
            "to_agent": "cursor",
            "desired_model": "auto",
            "desired_effort": "medium",
            "contract": "implement",
        },
    )


def test_thread_lane_counts_alone_is_zero() -> None:
    queue = AutoJobQueue(durable=False)
    job = _enqueue(queue, thread_id="6885", turn_number=1)
    counts = queue.thread_lane_counts("6885", exclude_job_id=job.job_id)
    assert counts["same_thread_pending"] == 0
    assert counts["same_thread_claimed"] == 0


def test_thread_lane_counts_queued_peers_are_thread_scoped() -> None:
    """N queued predecessors on one thread → N; other threads do not inflate."""
    queue = AutoJobQueue(durable=False)
    _enqueue(queue, thread_id="6885", turn_number=1)
    _enqueue(queue, thread_id="6885", turn_number=2)
    other = _enqueue(queue, thread_id="9999", turn_number=1)
    new = _enqueue(queue, thread_id="6885", turn_number=3)

    counts = queue.thread_lane_counts("6885", exclude_job_id=new.job_id)
    assert counts["same_thread_pending"] == 2
    assert counts["same_thread_claimed"] == 0
    other_counts = queue.thread_lane_counts("9999", exclude_job_id=other.job_id)
    assert other_counts["same_thread_pending"] == 0


def test_thread_lane_counts_claimed_peer() -> None:
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="6885", turn_number=1)
    assert queue.claim_next().job_id == old.job_id
    new = _enqueue(queue, thread_id="6885", turn_number=2)
    counts = queue.thread_lane_counts("6885", exclude_job_id=new.job_id)
    assert counts["same_thread_pending"] == 0
    assert counts["same_thread_claimed"] == 1


def test_enqueue_response_alone_same_thread_pending_zero(
    cursor_auto_client: TestClient,
) -> None:
    get_registry().register("6885-lane-count-handler")
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        resp = _post_enqueue(cursor_auto_client, thread_id="6885-a", turn_number=1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["same_thread_pending"] == 0
    assert body["same_thread_claimed"] == 0
    # Process-global queue may still show the just-enqueued job as pending.
    assert body["queue"]["pending"] >= 1


def test_enqueue_response_queued_predecessor_reports_pending(
    cursor_auto_client: TestClient,
) -> None:
    """Backed-up lane: second admit sees same_thread_pending=1 (must fail pre-fix)."""
    get_registry().register("6885-lane-count-handler")
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        first = _post_enqueue(cursor_auto_client, thread_id="6885-b", turn_number=1)
        second = _post_enqueue(cursor_auto_client, thread_id="6885-b", turn_number=2)
    assert first.status_code == 200
    assert first.json()["same_thread_pending"] == 0
    assert second.status_code == 200
    body = second.json()
    assert body["superseded"] is None
    assert body["same_thread_pending"] == 1
    assert body["same_thread_claimed"] == 0
    # Global pending is not the discriminant (includes self + peers).
    assert body["queue"]["pending"] == 2


def test_enqueue_response_claimed_predecessor_reports_claimed(
    cursor_auto_client: TestClient,
) -> None:
    get_registry().register("6885-lane-count-handler")
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        first = _post_enqueue(cursor_auto_client, thread_id="6885-c", turn_number=1)
        assert first.status_code == 200
        claimed = get_queue().claim_next()
        assert claimed is not None
        second = _post_enqueue(cursor_auto_client, thread_id="6885-c", turn_number=2)
    body = second.json()
    assert body["same_thread_pending"] == 0
    assert body["same_thread_claimed"] == 1
