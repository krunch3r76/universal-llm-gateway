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


def test_reconcile_holder_lost_subject_prefix_is_not_a_holder_id() -> None:
    """12680 shape: an 8-char generate prefix is not a holder id."""
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
    assert written == []
    assert posts == []

    held = reconcile_holder_lost(
        [lane],
        frozenset({"6e1f4eba-5e95-4e4f-94a3-91ec0835ac5c"}),
        post=fake_post,
    )
    assert held == []
    assert posts == []


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

    with patch(
        "bus_watch.quiet_reason.httpx.get", side_effect=httpx.ConnectError("down")
    ):
        held = fetch_held_execution_ids()
        assert held is None
        if held is not None:
            reconcile_holder_lost([_admitted_lane()], held, post=fake_post)
    assert posts == []


_P3_DISPATCH = "62cee299adf4-ed675f8c"
_P3_EXECUTION = "fc33e2ab-47e3-4b5b-a947-6226861d2a89"
_P3_PROJECTION = [
    {
        "thread_id": "12951",
        "op_id": _P3_DISPATCH,
        "dispatch_id": _P3_DISPATCH,
        "execution_id": "",
    }
]


def test_reconcile_holder_lost_12951_live_projection_thread_does_not_post() -> None:
    """Lane 12951, subject prefix fc33e2ab, held dispatch 62cee299… does not post."""
    posts: list[tuple[str, str]] = []

    def fake_post(thread_id: str, execution_id: str) -> bool:
        posts.append((thread_id, execution_id))
        return True

    prefix_only = {
        "id": "12951",
        "status": "active",
        "lifecycle": "admitted",
        "contract": "conductor",
        "last_subject": "cursor-sdk generate — fc33e2ab",
    }
    written = reconcile_holder_lost(
        [prefix_only],
        frozenset({_P3_DISPATCH}),
        projections=_P3_PROJECTION,
        post=fake_post,
    )
    assert written == []
    assert posts == []
    assert prefix_only["dispatch_id"] == _P3_DISPATCH
    assert prefix_only["live_projection_thread_id"] == "12951"
    assert prefix_only["dispatch_id"] != "fc33e2ab"

    mismatched = _admitted_lane(
        execution_id=_P3_EXECUTION,
        id="12951",
        last_subject="cursor-sdk generate — fc33e2ab",
    )
    skipped = reconcile_holder_lost(
        [mismatched],
        frozenset({_P3_DISPATCH}),
        projections=_P3_PROJECTION,
        post=fake_post,
    )
    assert skipped == []
    assert posts == []

    unmatched = _admitted_lane(
        execution_id=_P3_EXECUTION,
        id="12951",
        last_subject="cursor-sdk generate — fc33e2ab",
    )
    posted = reconcile_holder_lost(
        [unmatched],
        frozenset({_P3_DISPATCH}),
        projections=[],
        post=fake_post,
    )
    assert posted == ["12951"]
    assert posts == [("12951", _P3_EXECUTION)]


def test_lane_row_carries_dispatch_and_execution_id() -> None:
    from bus_watch.liaison_digest import _lane_row

    row = _lane_row(
        {
            "id": "12951",
            "status": "active",
            "last_subject": "cursor-sdk generate — fc33e2ab",
            "execution_id": _P3_EXECUTION,
            "dispatch_id": _P3_DISPATCH,
        }
    )
    assert row["execution_id"] == _P3_EXECUTION
    assert row["dispatch_id"] == _P3_DISPATCH

    prefix = _lane_row(
        {
            "id": "12951",
            "status": "active",
            "last_subject": "cursor-sdk generate — fc33e2ab",
            "execution_id": "fc33e2ab",
            "dispatch_id": "fc33e2ab",
        }
    )
    assert "execution_id" not in prefix
    assert "dispatch_id" not in prefix


def test_fetch_held_keeps_projection_thread_apart_from_dispatch_id() -> None:
    from bus_watch.quiet_reason import live_holder_projections

    payload = {
        "cursor_dispatches": {"dispatch_ids": [_P3_DISPATCH]},
        "active_ops": [
            {
                "kind": "cursor_sdk",
                "op_id": _P3_DISPATCH,
                "thread_id": "12951",
            }
        ],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return payload

    with patch("bus_watch.quiet_reason.httpx.get", return_value=_Response()):
        held = fetch_held_execution_ids()
    try:
        assert held == {_P3_DISPATCH}
        assert "12951" not in held
        assert "fc33e2ab" not in held
        projections = live_holder_projections()
        assert projections is not None
        assert projections[0]["thread_id"] == "12951"
        assert projections[0]["dispatch_id"] == _P3_DISPATCH
    finally:
        from bus_watch.quiet_reason import _remember_projections

        _remember_projections(None)
