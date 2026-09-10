"""Tests for lane-scoped TAB_READY liveness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cursor_bridge.lane_ready import (
    assess_lane_readiness,
    lane_epoch_turn,
    wait_for_lane_ready,
)


def _turn(
    n: int,
    subject: str,
    *,
    sender: str = "cursor",
    created_at: datetime | None = None,
) -> dict:
    ts = created_at or datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)
    return {
        "turn_number": n,
        "from": sender,
        "to": "web-anthropic" if sender == "cursor" else "cursor",
        "subject": subject,
        "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@pytest.mark.offline
def test_lane_epoch_after_bridge_open() -> None:
    turns = [
        _turn(3, "TAB_READY"),
        _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
        _turn(7, "TAB_READY", created_at=datetime(2026, 9, 10, 8, 10, 0, tzinfo=UTC)),
    ]
    assert lane_epoch_turn(turns) == 5


@pytest.mark.offline
def test_pre_bridge_tab_ready_not_live() -> None:
    now = datetime(2026, 9, 10, 8, 15, 0, tzinfo=UTC)
    turns = [
        _turn(3, "TAB_READY", created_at=datetime(2026, 9, 10, 8, 14, 0, tzinfo=UTC)),
        _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
    ]
    out = assess_lane_readiness(turns, now=now, ttl_s=600)
    assert out["ready"] is False
    assert out["reason"] == "no_tab_ready"


@pytest.mark.offline
def test_stale_tab_ready_fails_closed() -> None:
    ready_at = datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)
    now = ready_at + timedelta(seconds=601)
    turns = [
        _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
        _turn(9, "TAB_READY", created_at=ready_at),
    ]
    out = assess_lane_readiness(turns, now=now, ttl_s=600)
    assert out["ready"] is False
    assert out["reason"] == "tab_ready_stale"
    assert out["turn"] == 9


@pytest.mark.offline
def test_tab_gone_invalidates_ready() -> None:
    now = datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC)
    turns = [
        _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
        _turn(9, "TAB_READY", created_at=datetime(2026, 9, 10, 8, 4, 0, tzinfo=UTC)),
        _turn(10, "TAB_GONE"),
    ]
    out = assess_lane_readiness(turns, now=now, ttl_s=600)
    assert out["ready"] is False
    assert out["reason"] == "no_tab_ready"


@pytest.mark.offline
def test_wait_for_lane_ready_succeeds_when_signal_arrives() -> None:
    now = datetime(2026, 9, 10, 8, 2, 0, tzinfo=UTC)
    calls = 0

    def fetch() -> list[dict]:
        nonlocal calls
        calls += 1
        if calls < 2:
            return [_turn(5, "BRIDGE_OPEN", sender="web-anthropic")]
        return [
            _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
            _turn(7, "TAB_READY", created_at=datetime(2026, 9, 10, 8, 1, 30, tzinfo=UTC)),
        ]

    out = wait_for_lane_ready(fetch, timeout_s=1.0, poll_s=0.01, now=now, ttl_s=600)
    assert out["ready"] is True
    assert out["turn"] == 7
    assert calls >= 2


@pytest.mark.offline
def test_wait_for_lane_ready_times_out() -> None:
    now = datetime(2026, 9, 10, 8, 2, 0, tzinfo=UTC)

    def fetch() -> list[dict]:
        return [_turn(5, "BRIDGE_OPEN", sender="web-anthropic")]

    out = wait_for_lane_ready(fetch, timeout_s=0.05, poll_s=0.01, now=now, ttl_s=600)
    assert out["ready"] is False
    assert out["reason"] == "tab_ready_timeout"


@pytest.mark.offline
def test_tab_alive_refreshes_liveness() -> None:
    now = datetime(2026, 9, 10, 8, 20, 0, tzinfo=UTC)
    turns = [
        _turn(5, "BRIDGE_OPEN", sender="web-anthropic"),
        _turn(9, "TAB_READY", created_at=datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)),
        _turn(11, "TAB_ALIVE", created_at=datetime(2026, 9, 10, 8, 19, 0, tzinfo=UTC)),
    ]
    out = assess_lane_readiness(turns, now=now, ttl_s=600)
    assert out["ready"] is True
    assert out["turn"] == 11
