"""Tests for liaison digest attention and policy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bus_watch.liaison_digest import GEAR_PRESETS, build_digest, effective_policy


def _lane(lid: str, *, unread: int = 0, terminal: bool = False) -> dict:
    return {
        "id": lid,
        "slug": f"lane-{lid}",
        "status": "active",
        "lifecycle": "admitted",
        "lane_role": "sub_mission",
        "turns": 3,
        "unread": unread,
        "last_from": "cursor-sdk",
        "last_subject": "CLOSEOUT" if terminal else "open",
        "terminal": terminal,
        "updated_at": "2026-09-11T00:00:00Z",
    }


@patch("bus_watch.liaison_digest._watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
def test_attention_excludes_terminal_without_unread(
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
) -> None:
    """AC-1: harvested CLOSEOUT lanes do not appear in attention; unread lanes do."""
    lanes = [
        _lane("10493", unread=0, terminal=True),
        _lane("10496", unread=2, terminal=False),
    ]
    mock_bus.return_value.__enter__.return_value = MagicMock()
    mock_get.return_value = {
        "id": "10479",
        "turn_count": 10,
        "status": "active",
        "last_subject": "CHECKPOINT",
    }
    mock_child.return_value = lanes
    state: dict = {"policy": {}}
    digest = build_digest("10479", state, register="autonomous", budget_tokens=700000)
    ids = [lane["id"] for lane in digest["attention"]]
    assert "10493" not in ids
    assert "10496" in ids


def test_gear_three_disarmed_by_default() -> None:
    """A7: gear 3 must not imply policy.ready."""
    policy = effective_policy({"policy": {"gear": "3-wake-on-attention"}})
    assert policy["wake_on_attention_only"] is True
    assert policy["ready"] is False


def test_hop_cap_tracks_policy_override() -> None:
    state = {"policy": {"max_hops_per_night": 16}}
    digest_state = dict(state)
    with (
        patch("bus_watch.liaison_digest._watchers", return_value=[]),
        patch("bus_watch.liaison_digest._health", return_value="ok"),
        patch("bus_watch.liaison_digest._unread_toc", return_value=[]),
        patch("bus_watch.liaison_digest._child_lanes", return_value=[]),
        patch(
            "bus_watch.liaison_digest._get",
            return_value={"id": "10479", "turn_count": 1, "status": "active"},
        ),
        patch("bus_watch.liaison_digest._bus") as mock_bus,
    ):
        mock_bus.return_value.__enter__.return_value = MagicMock()
        digest = build_digest(
            "10479", digest_state, register="autonomous", budget_tokens=700000
        )
    assert digest["hop_cap"]["max_hops_per_night"] == 16
    assert GEAR_PRESETS["2-opus-hops"]["successor_model"] == "cursor/claude-opus-5"
