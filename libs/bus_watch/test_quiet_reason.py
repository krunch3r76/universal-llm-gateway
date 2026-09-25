"""Finished quiet lanes close; holder_lost reconcile for orphan admitted hops."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from bus_watch.quiet_reason import (
    close_unharvested_quiet_lanes,
    fetch_held_execution_ids,
    reconcile_holder_lost,
)


def test_close_unharvested_quiet_lane_only() -> None:
    lanes = [
        {"id": "12666", "status": "active", "quiet_reason": "closeout_unharvested"},
        {"id": "12650", "status": "active", "quiet_reason": "wip_in_flight"},
        {"id": "12672", "status": "closed", "quiet_reason": "closeout_unharvested"},
    ]
    patched: list[str] = []
    closed = close_unharvested_quiet_lanes(
        lanes, patch=lambda tid: patched.append(tid) or True
    )
    assert closed == ["12666"]
    assert patched == ["12666"]
    assert lanes[0]["status"] == "closed"
    assert lanes[1]["status"] == "active"


def _admitted_lane(*, execution_id: str = "exec-orphan-1", **extra: object) -> dict:
    lane = {
        "id": "12711",
        "status": "active",
        "lifecycle": "admitted",
        "contract": "conductor",
        "dispatch_id": execution_id,
        "last_subject": "cursor-sdk generate admitted",
    }
    lane.update(extra)
    return lane


def test_reconcile_holder_lost_posts_when_not_held() -> None:
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        subject = f"holder_lost {execution_id}"
        assert subject.startswith("holder_lost")
        posts.append((thread_id, subject))
        return True

    written = reconcile_holder_lost(
        [_admitted_lane()],
        frozenset(),
        post=fake_post,
    )
    assert written == ["12711"]
    assert posts == [("12711", "holder_lost exec-orphan-1")]


def test_reconcile_holder_lost_posts_digest_generate_subject() -> None:
    """12680 shape: digest lane has no dispatch_id, lifecycle active, short id."""
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        posts.append((thread_id, execution_id))
        return True

    lane = {
        "id": "12680",
        "status": "active",
        "lifecycle": "active",
        "contract": "conductor",
        "last_subject": "cursor-sdk generate — 6e1f4eba",
    }
    written = reconcile_holder_lost([lane], frozenset(), post=fake_post)
    assert written == ["12680"]
    assert posts == [("12680", "6e1f4eba")]

    held = reconcile_holder_lost(
        [lane],
        frozenset({"6e1f4eba-5e95-4e4f-94a3-91ec0835ac5c"}),
        post=fake_post,
    )
    assert held == []


def test_reconcile_holder_lost_skips_when_execution_held() -> None:
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        posts.append((thread_id, execution_id))
        return True

    written = reconcile_holder_lost(
        [_admitted_lane(execution_id="held-exec")],
        frozenset({"held-exec"}),
        post=fake_post,
    )
    assert written == []
    assert posts == []


def test_reconcile_holder_lost_skips_dispatch_orphaned_subject() -> None:
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        posts.append((thread_id, execution_id))
        return True

    written = reconcile_holder_lost(
        [
            _admitted_lane(
                last_subject="Dispatch orphaned — worker terminated before completion"
            )
        ],
        frozenset(),
        post=fake_post,
    )
    assert written == []
    assert posts == []


def test_fetch_held_execution_ids_get_failure_writes_nothing() -> None:
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        posts.append((thread_id, execution_id))
        return True

    with patch("bus_watch.quiet_reason.httpx.get", side_effect=httpx.ConnectError("down")):
        held = fetch_held_execution_ids()
        assert held is None
        if held is not None:
            reconcile_holder_lost([_admitted_lane()], held, post=fake_post)
    assert posts == []
