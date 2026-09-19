"""Tests for liaison digest attention and policy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.digest_budget import build_budget_block
from bus_watch.liaison_digest import (
    GEAR_PRESETS,
    _child_lanes,
    _lineage_lanes,
    _merge_observe_lanes,
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


@patch("bus_watch.liaison_digest._get")
def test_child_lanes_merged_listing_excludes_status_all(mock_get: MagicMock) -> None:
    """GET /threads rejects status=all (422); merge active + has_unread instead."""
    root = "10479"
    calls: list[dict] = []

    def fake_get(_client: object, path: str, **params: object) -> dict:
        calls.append(dict(params))
        if path == "/threads" and params.get("status") == "active":
            return {
                "threads": [
                    {
                        "id": "100",
                        "parent_thread": root,
                        "last_subject": "open work",
                        "unread_count": 1,
                        "last_turn_from": "cursor-sdk",
                        "updated_at": "2026-09-13T10:00:00Z",
                    }
                ]
            }
        if path == "/threads" and params.get("has_unread"):
            return {
                "threads": [
                    {
                        "id": "101",
                        "parent_thread": root,
                        "last_subject": "branch-debt aged: cursor-sdk/lane-10480",
                        "unread_count": 2,
                        "last_turn_from": "git-integration-worker",
                        "updated_at": "2026-09-13T09:00:00Z",
                    }
                ]
            }
        if path == "/turns":
            return {"turns": []}
        return {}

    mock_get.side_effect = fake_get
    lanes = _child_lanes(MagicMock(), root)
    assert not any(c.get("status") == "all" for c in calls)
    assert any(c.get("status") == "active" for c in calls)
    assert any(c.get("has_unread") is True for c in calls)
    nag = next(lane for lane in lanes if lane["id"] == "101")
    live = next(lane for lane in lanes if lane["id"] == "100")
    assert nag["nag"] is True
    assert live["nag"] is False
    assert lanes[-1]["id"] == "101"


@patch("bus_watch.liaison_digest._get")
def test_lineage_lanes_includes_closed_grandchild(mock_get: MagicMock) -> None:
    """Closed OLN commission + implement grandchild are invisible to _child_lanes."""

    def fake_get(_client: object, path: str, **_params: object) -> dict:
        if path == "/threads/11667/lineage":
            return {
                "children": [
                    {
                        "thread_id": "11693",
                        "status": "closed",
                        "lane_role": "sub_mission",
                    }
                ]
            }
        if path == "/threads/11693/lineage":
            return {
                "children": [
                    {
                        "thread_id": "11697",
                        "status": "closed",
                        "lane_role": "sub_mission",
                    }
                ]
            }
        if path == "/threads/11693":
            return {
                "id": "11693",
                "slug": "oln-lane-status-debrief",
                "status": "closed",
                "parent_thread": "11667",
                "bus_lifecycle_state": None,
                "last_subject": "DONE — OLN harness",
            }
        if path == "/threads/11697":
            return {
                "id": "11697",
                "slug": "cursor-sdk-generate-88d73707",
                "status": "closed",
                "parent_thread": "11693",
                "bus_lifecycle_state": "completed",
                "last_subject": "cursor-sdk CLOSEOUT",
            }
        return {}

    mock_get.side_effect = fake_get
    rows = _lineage_lanes(MagicMock(), "11667")
    ids = [row["id"] for row in rows]
    assert ids == ["11693", "11697"]
    assert rows[0]["status"] == "closed"
    assert rows[1]["status"] == "closed"
    live = [{"id": "11698", "status": "active"}]
    merged = _merge_observe_lanes(live, rows)
    assert [row["id"] for row in merged] == ["11698", "11693", "11697"]


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
def test_attention_excludes_nag_lanes(
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
) -> None:
    """branch-debt nag lanes stay in lanes (last) but never enter attention."""
    nag = {
        "id": "101",
        "slug": "nag",
        "status": "active",
        "lifecycle": "admitted",
        "lane_role": "sub_mission",
        "turns": 1,
        "unread": 2,
        "last_from": "git-integration-worker",
        "last_subject": "branch-debt aged: cursor-sdk/lane-10480",
        "terminal": False,
        "nag": True,
        "updated_at": "2026-09-13T09:00:00Z",
    }
    live = _lane("100", unread=1, terminal=False)
    live["nag"] = False
    mock_bus.return_value.__enter__.return_value = MagicMock()
    mock_get.return_value = {"id": "10479", "turn_count": 10, "status": "active"}
    mock_child.return_value = [live, nag]
    state: dict = {"policy": {}}
    digest = build_digest("10479", state, register="autonomous", budget_tokens=700000)
    attn_ids = [lane["id"] for lane in digest["attention"] if "id" in lane]
    assert "101" not in attn_ids
    assert "100" in attn_ids
    assert digest["lanes"][-1]["id"] == "101"
    assert digest["attention_nag_excluded"] == {
        "count": 1,
        "source": "liaison_digest._NAG_RE",
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


def test_checkpoint_observed_event_signal() -> None:
    """AC2.5 — event factory declares liaison.checkpoint.observed."""
    from bus_watch.events import LiaisonCheckpointObserved

    event = LiaisonCheckpointObserved(
        root="10479",
        turn=10,
        prior_turn=0,
        source="digest.root.tip_checkpoint_turn",
    )
    assert event.signal == "liaison.checkpoint.observed"


def test_gear4_successor_contract_conductor_gear3_none() -> None:
    """AC1.1 — gear-4 binds conductor; other gears keep none."""
    assert (
        effective_policy({"policy": {"gear": "4-cdp-liaison"}})["successor_contract"]
        == "conductor"
    )
    assert (
        effective_policy({"policy": {"gear": "3-wake-on-attention"}})[
            "successor_contract"
        ]
        == "none"
    )


def test_gear_three_disarmed_by_default() -> None:
    """A7: gear 3 must not imply policy.ready — the register does."""
    policy = effective_policy({"policy": {"gear": "3-wake-on-attention"}})
    assert policy["wake_on_attention_only"] is True
    assert policy["ready"] is False
    assert policy["ready_source"] == "default"


def test_gear3_register_never_arms_the_ticker() -> None:
    """An IDE-hop chain runs autonomous with the ticker policy-only; only an
    explicit ready (operator or --go-under) arms it."""
    autonomous = effective_policy(
        {"register": "autonomous", "policy": {"gear": "3-wake-on-attention"}}
    )
    assert autonomous["ready"] is False and autonomous["ready_source"] == "default"
    armed = effective_policy(
        {
            "register": "autonomous",
            "policy": {"gear": "3-wake-on-attention", "ready": True},
        }
    )
    assert armed["ready"] is True and armed["ready_source"] == "override"


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
    """AC-3: liaison_digest.py assembly cap (300 + root turn surface + R-a cache for C-R2-5)."""
    from pathlib import Path

    path = Path(__file__).resolve().parent / "liaison_digest.py"
    sloc = sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert sloc <= 400


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


def _digest_mocks(
    mock_bus: MagicMock,
    mock_get: MagicMock,
    *,
    tip_checkpoint_turn: int | None,
) -> None:
    mock_bus.return_value.__enter__.return_value = MagicMock()
    mock_get.return_value = {
        "id": "10479",
        "turn_count": 10,
        "status": "active",
        "last_subject": "CHECKPOINT",
    }


@patch("bus_watch.liaison_digest.emit_checkpoint_observed")
@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest.digest_root_surface")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_checkpoint_observed_advances_epoch_and_clears_due(
    _usage: object,
    _lock: object,
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_root_surface: MagicMock,
    _child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
    mock_emit: MagicMock,
) -> None:
    """AC2.1 — newer tip CHECKPOINT advances epoch; following build not due."""
    _digest_mocks(mock_bus, mock_get, tip_checkpoint_turn=None)
    mock_root_surface.return_value = ([], None, [])
    state: dict = {"ticks": 5, "last_cp_tick": 0, "last_cp_turn": 0, "policy": {}}
    due_before = build_digest(
        "10479", state, register="autonomous", budget_tokens=700000
    )
    assert due_before["checkpoint_due"] is True
    mock_root_surface.return_value = ([], 50, [])
    due_after = build_digest(
        "10479", state, register="autonomous", budget_tokens=700000
    )
    assert state["last_cp_turn"] == 50
    assert state["last_cp_tick"] == 7
    assert due_after["checkpoint_due"] is False
    mock_emit.assert_called_once_with(
        root="10479",
        turn=50,
        prior_turn=0,
        source="digest.root.tip_checkpoint_turn",
    )


@patch("bus_watch.liaison_digest.emit_checkpoint_observed")
@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest.digest_root_surface")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_checkpoint_observed_idempotent_same_turn(
    _usage: object,
    _lock: object,
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_root_surface: MagicMock,
    _child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
    mock_emit: MagicMock,
) -> None:
    """AC2.2 — unchanged tip CHECKPOINT does not re-emit."""
    _digest_mocks(mock_bus, mock_get, tip_checkpoint_turn=40)
    mock_root_surface.return_value = ([], 40, [])
    state: dict = {"ticks": 1, "last_cp_turn": 40, "last_cp_tick": 2, "policy": {}}
    before = dict(state)
    build_digest("10479", state, register="autonomous", budget_tokens=700000)
    assert state["last_cp_turn"] == before["last_cp_turn"]
    assert state["last_cp_tick"] == before["last_cp_tick"]
    mock_emit.assert_not_called()


@patch("bus_watch.liaison_digest.emit_checkpoint_observed")
@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest.digest_root_surface")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_checkpoint_absent_leaves_epoch_untouched(
    _usage: object,
    _lock: object,
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_root_surface: MagicMock,
    _child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
    mock_emit: MagicMock,
) -> None:
    """AC2.3 — no tip CHECKPOINT leaves today's behavior."""
    _digest_mocks(mock_bus, mock_get, tip_checkpoint_turn=None)
    mock_root_surface.return_value = ([], None, [])
    state: dict = {"ticks": 3, "last_cp_tick": 1, "policy": {}}
    before = dict(state)
    build_digest("10479", state, register="autonomous", budget_tokens=700000)
    assert state.get("last_cp_turn") == before.get("last_cp_turn")
    assert state["last_cp_tick"] == before["last_cp_tick"]
    mock_emit.assert_not_called()


@patch("bus_watch.liaison_digest.emit_checkpoint_observed")
@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest.digest_root_surface")
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest.read_lock", return_value={"holder": None})
@patch("bus_watch.digest_budget._read_sdk_usage_live", return_value=None)
def test_manual_last_cp_tick_not_lowered(
    _usage: object,
    _lock: object,
    mock_bus: MagicMock,
    mock_get: MagicMock,
    mock_root_surface: MagicMock,
    _child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
    mock_emit: MagicMock,
) -> None:
    """AC2.4 — manual mark ahead of ticks is preserved via max()."""
    _digest_mocks(mock_bus, mock_get, tip_checkpoint_turn=10)
    mock_root_surface.return_value = ([], 10, [])
    state: dict = {"ticks": 2, "last_cp_tick": 100, "last_cp_turn": 0, "policy": {}}
    build_digest("10479", state, register="autonomous", budget_tokens=700000)
    assert state["last_cp_tick"] == 100
    assert state["last_cp_turn"] == 10
