"""Tests for liaison digest attention and policy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.digest_budget import build_budget_block
from bus_watch.liaison_digest import (
    GEAR_PRESETS,
    build_digest,
    effective_policy,
    is_life_root,
)


@pytest.fixture(autouse=True)
def _no_real_ide_tab():
    """The IDE budget reads ~/.cursor transcripts; keep these digests hermetic."""
    with patch("bus_watch.liaison_digest._measure_ide_tab", return_value=None):
        yield


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


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
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
    _watchers: MagicMock,  # collect_watchers
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
    ids = [lane["id"] for lane in digest["attention"] if "id" in lane]
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
        patch("bus_watch.liaison_digest.collect_watchers", return_value=[]),
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
    assert digest["hop_cap"]["lock_hops_scope"] == "root"
    assert "lock_hops" in digest["hop_cap"]
    assert GEAR_PRESETS["2-opus-hops"]["successor_model"] == "cursor/claude-opus-5"


def test_budget_block_estimate_source() -> None:
    budget = build_budget_block(
        used_tokens=1000,
        window_limit_tokens=700000,
        model="cursor/claude-opus-5",
        source="digest.estimate",
        scope="liaison_seat",
        epoch="fp-abc",
        as_of="2026-09-11T00:00:00Z",
    )
    assert set(budget.keys()) == {
        "used_tokens",
        "window_limit_tokens",
        "model",
        "as_of",
        "source",
        "scope",
        "epoch",
        "stop_class",
    }
    assert budget["source"] == "digest.estimate"
    assert budget["stop_class"] is None


def test_budget_block_sdk_stream_context_budget() -> None:
    budget = build_budget_block(
        used_tokens=600000,
        window_limit_tokens=700000,
        model="cursor/claude-fable-5-1",
        source="giw.sdk_stream",
        scope="liaison_seat",
        epoch="dispatch-1",
        as_of="2026-09-11T00:00:00Z",
    )
    assert budget["stop_class"] == "CONTEXT_BUDGET"


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_budget_estimate_attention_item(
    _usage: object,
    _lock: object,
    mock_bus: object,
    mock_get: object,
    _child: object,
    _toc: object,
    _health: object,
    _watchers: object,
) -> None:
    mock_get.return_value = {
        "id": "10479",
        "turn_count": 10,
        "status": "active",
        "last_subject": "CHECKPOINT",
    }
    mock_bus.return_value.__enter__.return_value = object()
    state: dict = {"policy": {}, "budget_epoch": "epoch-1"}
    digest = build_digest("10479", state, register="autonomous", budget_tokens=700000)
    kinds = [item.get("kind") for item in digest["attention"]]
    assert "budget_estimate" in kinds
    assert digest["budget"]["source"] == "digest.estimate"
    assert digest["budget"]["stop_class"] is None


def test_watchers_exclude_foreign_root_with_fresh_mtime(tmp_path) -> None:  # noqa: ANN001
    """Foreign-root complete watcher with fresh mtime must not appear on this house."""
    import os
    import time

    from bus_watch.liaison_watchers import collect_watchers

    root_id = "10534"
    other = "10479"
    state = {"born_epoch": time.time() - 3600, "relayed_watchers": []}
    lane_ids = {root_id}

    foreign_path = tmp_path / f"{other}-watcher.state.json"
    foreign_path.write_text(
        '{"thread": "99999", "status": "complete", "label": "foreign"}',
        encoding="utf-8",
    )
    os.utime(foreign_path, (time.time(), time.time()))

    own_path = tmp_path / f"{root_id}-child.state.json"
    own_path.write_text(
        f'{{"thread": "{root_id}", "status": "complete"}}',
        encoding="utf-8",
    )

    result = collect_watchers(state, lane_ids, root_id, tmp_path)
    names = [w["file"] for w in result]
    assert foreign_path.name not in names
    assert own_path.name in names


def test_liaison_digest_sloc_cap() -> None:
    """AC-3: liaison_digest.py stays within the 300 SLOC assembly cap."""
    from pathlib import Path

    path = Path(__file__).resolve().parent / "liaison_digest.py"
    sloc = sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert sloc <= 300


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_code_root_digest_omits_life_key(
    _usage: object,
    _lock: object,
    mock_bus: object,
    mock_get: object,
    _child: object,
    _toc: object,
    _health: object,
    _watchers: object,
) -> None:
    """AC-4: 10479-shaped liaison root has no life key."""
    mock_get.return_value = {
        "id": "10479",
        "turn_count": 10,
        "status": "active",
        "tags": ["lane:liaison"],
        "last_subject": "CHECKPOINT",
    }
    mock_bus.return_value.__enter__.return_value = object()
    state: dict = {"policy": {}}
    digest = build_digest("10479", state, register="autonomous", budget_tokens=700000)
    assert "life" not in digest
    assert is_life_root(mock_get.return_value) is False


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_life_root_digest_attaches_life_block(
    _usage: object,
    _lock: object,
    mock_bus: object,
    mock_get: object,
    _child: object,
    _toc: object,
    _health: object,
    _watchers: object,
) -> None:
    """AC-4: lane:life root carries projected life block with F3-default gates."""
    mock_get.return_value = {
        "id": "10500",
        "turn_count": 3,
        "status": "active",
        "tags": ["lane:life"],
        "last_subject": "CHECKPOINT",
    }
    mock_bus.return_value.__enter__.return_value = object()
    state: dict = {"policy": {}}
    digest = build_digest("10500", state, register="autonomous", budget_tokens=700000)
    assert is_life_root(mock_get.return_value) is True
    assert "life" in digest
    assert digest["life"]["now"] is None
    assert "gates" in digest["life"]
