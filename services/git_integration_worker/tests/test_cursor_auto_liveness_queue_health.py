"""S-4 observability -- /liveness queue_health projection (mission 9440)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.liveness import (
    _OCCUPANT_IDLE_RED_THRESHOLD_S,
    get_registry,
    queue_admission_health,
    reset_queue_health_red_edge_for_tests,
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
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    reset_queue_health_red_edge_for_tests()
    yield
    AutoJobLedger.reset_for_tests()


def _enqueue(
    queue: AutoJobQueue,
    *,
    execution_mode: str = "serial",
    thread_id: str = "9440",
    turn: int = 1,
):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="investigate",
        execution_mode=execution_mode,
    )


def _backdate_heartbeat(job_id: str, *, seconds_ago: float) -> None:
    """Directly age a job's ledger heartbeat past the red threshold.

    No public ledger API exists for backdating (by design -- heartbeats are
    always written as "now"); raw SQL against the durable row is the
    documented escape hatch for this one test (packet 9440-g7, section 4.6
    test 3).
    """
    stale = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    with get_ledger()._connect() as conn:
        conn.execute(
            "UPDATE cursor_auto_jobs SET last_heartbeat_at=? WHERE job_id=?",
            (stale, job_id),
        )


def test_queue_health_green_when_no_pending() -> None:
    health = queue_admission_health()
    assert health["admit_eligible_pending"] == 0
    assert health["red"] is False
    assert health["red_reason"] is None


def test_queue_health_green_when_occupant_heartbeating_despite_long_queue() -> None:
    queue = get_queue()
    occupant = _enqueue(queue, turn=1)
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.job_id == occupant.job_id
    queue.bump_heartbeat(occupant.job_id)
    _enqueue(queue, turn=2)  # a second pending job -- long queue, busy occupant

    health = queue_admission_health()
    assert health["red"] is False
    assert health["red_reason"] is None
    assert health["occupant_idle_s"] is not None
    assert health["occupant_idle_s"] < 1.0
    assert health["amber"] is False
    assert health["oldest_waiter_age_s"] is not None
    assert health["oldest_waiter_age_s"] < WAITER_STARVATION_AMBER_THRESHOLD_S


def test_queue_health_red_when_occupant_heartbeat_stale() -> None:
    queue = get_queue()
    occupant = _enqueue(queue, turn=1)
    claimed = queue.claim_next()
    assert claimed is not None
    _backdate_heartbeat(occupant.job_id, seconds_ago=_OCCUPANT_IDLE_RED_THRESHOLD_S + 5)
    _enqueue(queue, turn=2)

    health = queue_admission_health()
    assert health["red"] is True
    assert health["red_reason"] == "occupant_idle"
    assert health["occupant_idle_s"] > _OCCUPANT_IDLE_RED_THRESHOLD_S


def test_queue_health_green_when_pending_but_no_occupant_claimed() -> None:
    queue = get_queue()
    _enqueue(queue, turn=1)  # queued, never claimed -- no occupant to be stuck

    health = queue_admission_health()
    assert health["red"] is False
    assert health["red_reason"] is None
    assert health["serial_occupant_job_id"] is None


def test_queue_health_excludes_concurrent_class_from_admit_eligible_pending() -> None:
    from services.git_integration_worker.cursor_auto.execution_mode import (
        LEASE_FREE_PROPAGATE_MODE,
    )

    queue = get_queue()
    _enqueue(queue, execution_mode=LEASE_FREE_PROPAGATE_MODE, turn=1)

    health = queue_admission_health()
    assert health["admit_eligible_pending"] == 0


def test_queue_health_red_when_waiter_starved_even_if_occupant_heartbeating() -> None:
    queue = get_queue()
    occupant = _enqueue(queue, turn=1)
    claimed = queue.claim_next()
    assert claimed is not None
    queue.bump_heartbeat(occupant.job_id)
    waiter = _enqueue(queue, turn=2)
    stale = (
        datetime.now(UTC) - timedelta(seconds=WAITER_STARVATION_AMBER_THRESHOLD_S + 15)
    ).isoformat()
    with get_ledger()._connect() as conn:
        conn.execute(
            "UPDATE cursor_auto_jobs SET enqueued_at=? WHERE job_id=?",
            (stale, waiter.job_id),
        )

    health = queue_admission_health()
    assert health["amber"] is True
    assert health["red"] is True
    assert health["red_reason"] == "waiter_starvation"
    assert health["occupant_idle_s"] is not None
    assert health["occupant_idle_s"] < 1.0


def test_liveness_route_existing_keys_unchanged() -> None:
    get_registry().register("test-handler")
    snapshot = get_registry().snapshot()
    for key in (
        "live",
        "lane",
        "handler_count",
        "handlers",
        "heartbeat_ttl_s",
        "uptime_s",
        "pid",
        "code_version",
        "wire_skew_aggregate",
    ):
        assert key in snapshot


def test_queue_not_serving_emits_on_rising_edge_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.queue_health_events."
        "emit_queue_not_serving",
        _capture,
    )
    queue = get_queue()
    occupant = _enqueue(queue, turn=1)
    claimed = queue.claim_next()
    assert claimed is not None
    queue.bump_heartbeat(occupant.job_id)
    waiter = _enqueue(queue, turn=2)
    stale = (
        datetime.now(UTC) - timedelta(seconds=WAITER_STARVATION_AMBER_THRESHOLD_S + 15)
    ).isoformat()
    with get_ledger()._connect() as conn:
        conn.execute(
            "UPDATE cursor_auto_jobs SET enqueued_at=? WHERE job_id=?",
            (stale, waiter.job_id),
        )

    first = queue_admission_health()
    assert first["red"] is True
    assert len(calls) == 1
    assert calls[0]["red_reason"] == "waiter_starvation"
    second = queue_admission_health()
    assert second["red"] is True
    assert len(calls) == 1
