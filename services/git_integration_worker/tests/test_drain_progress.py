"""Unit tests for the R1′ / R2′ occupancy progress predicate."""

from __future__ import annotations

from services.git_integration_worker.drain_progress import (
    COMPLETED_UNCONSUMED_GRACE_S,
    HEARTBEAT_TTL_S,
    STALL_WINDOW_S,
    OccupancyProgressTracker,
    heartbeat_fresh,
    is_progress,
    occupancy_op_ids,
    stalled_from_terms,
)


def test_turnover_without_heartbeat_is_progress() -> None:
    """Tickets have no heartbeat; op-set change is their progress term."""
    assert is_progress(
        prev_count=2,
        prev_ids=frozenset({"a", "b"}),
        count=2,
        ids=frozenset({"a", "c"}),
        ops=[{"op_id": "a"}, {"op_id": "c"}],
    )


def test_count_decrease_is_progress() -> None:
    assert is_progress(
        prev_count=2,
        prev_ids=frozenset({"a", "b"}),
        count=1,
        ids=frozenset({"a"}),
        ops=[{"op_id": "a"}],
    )


def test_fresh_heartbeat_is_progress_without_turnover() -> None:
    ops = [{"op_id": "job-1", "heartbeat_age_s": 1.0}]
    assert heartbeat_fresh(ops, ttl_s=15.0)
    assert is_progress(
        prev_count=1,
        prev_ids=frozenset({"job-1"}),
        count=1,
        ids=frozenset({"job-1"}),
        ops=ops,
    )


def test_stale_heartbeat_same_set_is_not_progress() -> None:
    ops = [{"op_id": "job-1", "heartbeat_age_s": 120.0}]
    assert not is_progress(
        prev_count=1,
        prev_ids=frozenset({"job-1"}),
        count=1,
        ids=frozenset({"job-1"}),
        ops=ops,
    )


def test_sdk_poll_30s_is_fresh_under_default_ttl() -> None:
    """9569 class: ~30s SDK poll is progress under HEARTBEAT_TTL_S ≥ 90."""
    assert HEARTBEAT_TTL_S >= 90.0
    assert STALL_WINDOW_S >= 90.0
    ops = [{"op_id": "sdk-1", "heartbeat_age_s": 30.0}]
    assert heartbeat_fresh(ops)
    assert is_progress(
        prev_count=1,
        prev_ids=frozenset({"sdk-1"}),
        count=1,
        ids=frozenset({"sdk-1"}),
        ops=ops,
    )


def test_stalled_occupied_requires_r1() -> None:
    assert stalled_from_terms(count=1, r1_stalled=True, completed_unconsumed=False)
    assert not stalled_from_terms(count=1, r1_stalled=False, completed_unconsumed=True)


def test_stalled_idle_requires_unconsumed() -> None:
    assert stalled_from_terms(count=0, r1_stalled=True, completed_unconsumed=True)
    assert not stalled_from_terms(count=0, r1_stalled=True, completed_unconsumed=False)


def test_tracker_grace_then_stall() -> None:
    t = OccupancyProgressTracker(stall_window_s=10.0)
    ops = [{"op_id": "ticket-1"}]
    t.reset(0.0)
    assert not t.stalled(ops, now_mono=5.0)
    assert t.stalled(ops, now_mono=10.0)


def test_tracker_heartbeat_resets_stall_clock() -> None:
    t = OccupancyProgressTracker(stall_window_s=10.0, heartbeat_ttl_s=15.0)
    t.reset(0.0)
    fresh = [{"op_id": "auto-1", "heartbeat_age_s": 1.0}]
    assert not t.stalled(fresh, now_mono=20.0)


def test_tracker_completed_unconsumed_past_grace() -> None:
    t = OccupancyProgressTracker(completed_grace_s=COMPLETED_UNCONSUMED_GRACE_S)
    t.reset(0.0)
    t.note_completed(1.0)
    assert not t.stalled([], now_mono=10.0)
    assert t.stalled([], now_mono=1.0 + COMPLETED_UNCONSUMED_GRACE_S)


def test_occupancy_op_ids_skips_missing() -> None:
    assert occupancy_op_ids([{"op_id": "a"}, {}, {"op_id": "b"}]) == frozenset(
        {"a", "b"}
    )
