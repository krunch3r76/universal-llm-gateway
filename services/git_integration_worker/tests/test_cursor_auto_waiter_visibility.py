"""Waiter-visible FIFO position + queued age (todo:cursor-auto-queue-visibility)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.liveness import (
    _OCCUPANT_IDLE_RED_THRESHOLD_S,
    get_registry,
    queue_admission_health,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.waiter_visibility import (
    WAITER_STARVATION_AMBER_THRESHOLD_S,
)


@pytest.fixture(autouse=True)
def _isolated_auto_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CURSOR_AUTO_HOP_WATCHES_PATH",
        str(tmp_path / "hop_cadence_watches.json"),
    )
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _enqueue(*, thread_id: str = "9501", turn: int = 1):
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


def _backdate_enqueued(job_id: str, *, seconds_ago: float) -> None:
    stale = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    with get_ledger()._connect() as conn:
        conn.execute(
            "UPDATE cursor_auto_jobs SET enqueued_at=? WHERE job_id=?",
            (stale, job_id),
        )


def test_fifo_position_is_one_indexed_among_queued_serial_jobs() -> None:
    first = _enqueue(turn=1)
    second = _enqueue(turn=2)
    third = _enqueue(turn=3)
    view1 = get_ledger().observer_state(job_id=first.job_id)
    view2 = get_ledger().observer_state(job_id=second.job_id)
    view3 = get_ledger().observer_state(job_id=third.job_id)
    assert view1 is not None and view1["queue_position"] == 1
    assert view2 is not None and view2["queue_position"] == 2
    assert view3 is not None and view3["queue_position"] == 3
    assert view1["queued_age_s"] is not None
    assert view1["queued_age_s"] < 2.0


def test_claimed_job_has_null_queue_position() -> None:
    occupant = _enqueue(turn=1)
    waiter = _enqueue(turn=2)
    claimed = get_queue().claim_next()
    assert claimed is not None and claimed.job_id == occupant.job_id
    occ_view = get_ledger().observer_state(job_id=occupant.job_id)
    wait_view = get_ledger().observer_state(job_id=waiter.job_id)
    assert occ_view is not None
    assert occ_view["queue_position"] is None
    assert occ_view["queued_age_s"] is None
    assert wait_view is not None
    assert wait_view["queue_position"] == 1


def test_amber_waiter_does_not_flip_occupant_idle_red() -> None:
    occupant = _enqueue(turn=1)
    claimed = get_queue().claim_next()
    assert claimed is not None
    get_queue().bump_heartbeat(occupant.job_id)
    waiter = _enqueue(turn=2)
    _backdate_enqueued(
        waiter.job_id, seconds_ago=WAITER_STARVATION_AMBER_THRESHOLD_S + 15
    )
    health = queue_admission_health()
    assert health["red"] is False
    assert health["occupant_idle_s"] is not None
    assert health["occupant_idle_s"] < 1.0
    assert health["amber"] is True
    assert health["oldest_waiter_age_s"] > WAITER_STARVATION_AMBER_THRESHOLD_S
    assert health["red_threshold_s"] == _OCCUPANT_IDLE_RED_THRESHOLD_S
    assert health["amber_threshold_s"] == WAITER_STARVATION_AMBER_THRESHOLD_S
    assert health["projection_only"] is True


def test_enqueue_receipt_and_job_state_share_position() -> None:
    get_registry().register("9501-waiter-handler")
    client = TestClient(create_app())
    payload = {
        "turn_number": 1,
        "subject": "turn 1",
        "body": "TYPE: DIRECTIVE\n",
        "from_agent": "web-anthropic",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "medium",
        "contract": "implement",
    }
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        first = client.post(
            "/api/v1/git/cursor-auto/enqueue",
            json={**payload, "thread_id": "9501-receipt"},
        )
        second = client.post(
            "/api/v1/git/cursor-auto/enqueue",
            json={**payload, "thread_id": "9501-receipt-b"},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["queue_position"] == 1
    assert second.json()["queue_position"] == 2
    assert first.json()["queued_age_s"] is not None
    job_id = second.json()["job_id"]
    state = client.get(
        "/api/v1/git/cursor-auto/job-state",
        params={"job_id": job_id},
    )
    assert state.status_code == 200
    body = state.json()
    assert body["found"] is True
    assert body["job"]["queue_position"] == 2
    queue = client.get("/api/v1/git/cursor-auto/queue")
    assert queue.status_code == 200
    assert "oldest_waiter_age_s" in queue.json()
    live = client.get("/api/v1/git/cursor-auto/liveness")
    assert live.status_code == 200
    qh = live.json()["queue_health"]
    assert qh["red"] is False
    assert "amber" in qh
    assert qh["projection_only"] is True


def test_durable_false_receipt_still_exposes_position() -> None:
    queue = AutoJobQueue(durable=False)
    kwargs = {
        "thread_id": "9501-mem",
        "subject": "t",
        "body": "TYPE: DIRECTIVE\n",
        "from_agent": "web-anthropic",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "medium",
        "contract": "implement",
    }
    a = queue.enqueue(turn_number=1, **kwargs)
    b = queue.enqueue(turn_number=2, **kwargs)
    assert queue.waiter_receipt(a.job_id)["queue_position"] == 1
    assert queue.waiter_receipt(b.job_id)["queue_position"] == 2
    snap = queue.snapshot()
    assert snap["oldest_waiter_age_s"] is not None
    assert snap["amber"] is False
