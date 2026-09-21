"""Tests for gear-3 ticker NOW row bind hook."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bus_watch.now_row import resolve_now_row
from bus_watch.now_row_bind import maybe_bind_now_row
from bus_watch.spawn_wake.play_classify import classify_leftover
from bus_watch.tick_state import (
    absorb_operator_edits,
    load_state,
    save_state,
    update_state,
)

pytestmark = pytest.mark.offline

AS_OF = "2026-09-21T10:00:00Z"
ROOT = "12043"
LANE_A = "12045"
LANE_B = "12044"


def _lane(
    lane_id: str,
    *,
    subject: str = "G3 generate admitted",
    unread: int = 1,
    terminal: bool = False,
    lifecycle: str = "admitted",
) -> dict:
    return {
        "id": lane_id,
        "lane_role": "sub_mission",
        "unread": unread,
        "last_subject": subject,
        "updated_at": "2026-09-21T09:00:00Z",
        "terminal": terminal,
        "lifecycle": lifecycle,
        "status": "active",
    }


def _digest(
    *,
    attention=None,
    lanes=None,
    policy=None,
    entity_cache=None,
    root_error=None,
    judgment_turns=None,
) -> dict:
    att = attention if attention is not None else [_lane(LANE_A)]
    lane_list = lanes if lanes is not None else list(att)
    digest: dict = {
        "root": {"id": ROOT, "turns": 33},
        "lanes": lane_list,
        "attention": att,
        "policy": dict(policy or {}),
        "policy_entity_cache": dict(entity_cache or {}),
        "summary_row": "",
        "frictions": [],
    }
    if root_error is not None:
        digest["root"]["error"] = root_error
    if judgment_turns is not None:
        digest["root"]["unread_turns"] = judgment_turns
    return digest


def _state(*, policy=None, bind=None) -> dict:
    st: dict = {"policy": dict(policy or {})}
    if bind is not None:
        st["now_row_bind"] = bind
    return st


def _write_state(path: Path, state: dict) -> None:
    save_state(path, state)


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    p = tmp_path / "liaison.tick.json"
    _write_state(p, {"policy": {}})
    return p


def test_bound_from_attention_writes_policy_not_set_at(state_path: Path) -> None:
    digest = _digest()
    state = _state()
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "bound"
    disk = load_state(state_path)
    row = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    assert disk["policy"]["now_row"] == row
    assert disk["now_row_bind"]["lane_id"] == LANE_A
    assert state["policy"]["now_row"] == row
    assert "now_row_set_at" not in disk
    bound.assert_called_once()


def test_pin_survives_lane_read(state_path: Path) -> None:
    row = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    digest = _digest(attention=[], lanes=[_lane(LANE_A, unread=0)])
    digest["policy"]["now_row"] = row
    state = _state(
        policy={"now_row": row},
        bind={"row": row, "lane_id": LANE_A, "tier": "attention"},
    )
    _write_state(state_path, state)
    raw, source = resolve_now_row(digest)
    assert source == "policy"
    assert raw == row


@pytest.mark.parametrize(
    ("subject",),
    [
        ("G3 LAND abc123",),
        ("LANE CLOSEOUT 12045",),
        ("TYPE: CLOSEOUT",),
        ("CHECKPOINT #7",),
        ("HOLD_MERGE 10561",),
        ("LAND OWED 10561",),
        ("quiet",),
        ("arm: none",),
    ],
)
def test_spent_subject_gate_negatives(
    state_path: Path, subject: str
) -> None:
    before = load_state(state_path)
    digest = _digest(attention=[_lane(LANE_A, subject=subject)])
    state = _state()
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound, patch(
        "bus_watch.now_row_bind.emit_now_row_released"
    ) as released:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] in {"none", "refused"}
    assert load_state(state_path) == before
    bound.assert_not_called()
    released.assert_not_called()


@pytest.mark.parametrize(
    ("subject", "terminal", "lifecycle", "attention_is_lane"),
    [
        ("G3 LAND abc123", False, "admitted", False),
        ("G3 generate", True, "admitted", False),
        ("G3 generate", False, "completed", False),
        ("HOLD_MERGE 10561", False, "admitted", True),
    ],
)
def test_release_on_spent_lane(
    state_path: Path,
    subject: str,
    terminal: bool,
    lifecycle: str,
    attention_is_lane: bool,
) -> None:
    row = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    lane = _lane(
        LANE_A,
        subject=subject,
        unread=1 if attention_is_lane else 0,
        terminal=terminal,
        lifecycle=lifecycle,
    )
    digest = _digest(
        attention=[lane] if attention_is_lane else [],
        lanes=[lane],
    )
    state = _state(
        policy={"now_row": row},
        bind={"row": row, "lane_id": LANE_A, "tier": "attention"},
    )
    _write_state(state_path, state)
    update_calls: list[int] = []

    def _count_update(path, mutate):  # noqa: ANN001
        update_calls.append(1)
        return update_state(path, mutate)

    with patch("bus_watch.now_row_bind.emit_now_row_released") as released, patch(
        "bus_watch.now_row_bind.update_state", side_effect=_count_update
    ):
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    if attention_is_lane:
        assert result["action"] == "refused"
        assert result["reason"] == "hold_marker"
        assert result.get("released") == "hold_marker"
    else:
        assert result["action"] == "none"
        assert result.get("released")
    disk = load_state(state_path)
    assert disk["policy"]["now_row"] == ""
    assert "now_row_bind" not in disk
    assert state["policy"]["now_row"] == ""
    assert "now_row_bind" not in state
    assert len(update_calls) == 1
    released.assert_called_once()
    if attention_is_lane:
        assert released.call_args.kwargs["reason"] == "hold_marker"


def test_release_then_rebind_same_tick(state_path: Path) -> None:
    row_a = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    row_b = f"agent-bus:{LANE_B} · «G3 implement packet»"
    digest = _digest(
        attention=[_lane(LANE_B, subject="G3 implement packet")],
        lanes=[
            _lane(LANE_A, subject="G3 LAND done", unread=0),
            _lane(LANE_B, subject="G3 implement packet"),
        ],
    )
    state = _state(
        policy={"now_row": row_a},
        bind={"row": row_a, "lane_id": LANE_A, "tier": "attention"},
    )
    _write_state(state_path, state)
    update_calls: list[int] = []

    def _count_update(path, mutate):  # noqa: ANN001
        from bus_watch.tick_state import update_state as real_update

        update_calls.append(1)
        return real_update(path, mutate)

    with patch("bus_watch.now_row_bind.update_state", side_effect=_count_update):
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "bound"
    assert result["superseded"] == row_a
    assert state["policy"]["now_row"] == row_b
    assert len(update_calls) == 1


def test_operator_bind_is_sovereign(state_path: Path) -> None:
    digest = _digest()
    state = _state(policy={"now_row": "R10 wake induction transport"})
    _write_state(state_path, state)
    before = json.dumps(load_state(state_path), sort_keys=True)
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "kept_operator_bind"
    assert json.dumps(load_state(state_path), sort_keys=True) == before
    bound.assert_not_called()


def test_set_overrides_and_ticker_backs_off(state_path: Path) -> None:
    row = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    digest = _digest(
        lanes=[_lane(LANE_A, subject="G3 LAND done", unread=0, lifecycle="completed")],
        attention=[],
    )
    state = _state(
        policy={"now_row": row},
        bind={"row": row, "lane_id": LANE_A, "tier": "attention"},
    )
    from bus_watch.tick_state import update_state

    update_state(
        state_path,
        lambda fresh: fresh.setdefault("policy", {}).update({"now_row": "quiet"}),
    )
    state["policy"]["now_row"] = "quiet"
    state.pop("now_row_bind", None)
    with patch("bus_watch.now_row_bind.emit_now_row_released") as released:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "kept_operator_bind"
    released.assert_not_called()
    assert load_state(state_path)["policy"]["now_row"] == "quiet"


def test_unsatisfied_todo_bind_is_released(state_path: Path) -> None:
    digest = _digest(entity_cache={"todo:x": "closed"})
    state = _state(policy={"now_row": "todo:x"})
    _write_state(state_path, state)
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "bound"
    assert result["superseded"] == "todo:x"
    bound.assert_called_once()


def test_judgment_not_persisted(state_path: Path) -> None:
    digest = _digest(
        attention=[_lane(LANE_A)],
        policy={},
        judgment_turns=[
            {
                "turn_number": 34,
                "from": "web",
                "subject": "cdp reply — abc",
                "body": "TYPE: JUDGMENT",
            }
        ],
    )
    with patch(
        "bus_watch.now_row.judgment_now_row",
        return_value=("11738#36 — cdp reply", "judgment"),
    ):
        state = _state()
        before = load_state(state_path)
        with patch("bus_watch.now_row_bind.emit_now_row_bound"):
            result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result == {"action": "none", "source": "judgment"}
    assert load_state(state_path) == before


def test_operator_race_aborts_write(state_path: Path) -> None:
    digest = _digest()
    state = _state()
    update_state(
        state_path,
        lambda fresh: fresh.setdefault("policy", {}).update({"now_row": "operator row"}),
    )
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "operator_raced"
    assert load_state(state_path)["policy"]["now_row"] == "operator row"
    bound.assert_not_called()


def test_empty_tick_writes_nothing(state_path: Path) -> None:
    digest = _digest(attention=[], lanes=[])
    state = _state()
    before = load_state(state_path)
    with patch("bus_watch.now_row_bind.emit_now_row_bound") as bound, patch(
        "bus_watch.now_row_bind.emit_now_row_released"
    ) as released:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result == {"action": "none", "source": "empty"}
    assert load_state(state_path) == before
    bound.assert_not_called()
    released.assert_not_called()


def test_play_gate_unchanged(state_path: Path) -> None:
    digest = _digest(attention=[_lane(LANE_A)])
    state = _state()
    maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert classify_leftover(digest, state)["leftover"] == "sit"

    digest_todo = _digest(attention=[], lanes=[])
    digest_todo["policy"]["now_row"] = "todo:foo"
    state_todo = _state(policy={"now_row": "todo:foo"})
    assert classify_leftover(digest_todo, state_todo)["leftover"] == "play"


def test_absorb_after_bind_is_noop(state_path: Path) -> None:
    digest = _digest()
    state = _state()
    maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    disk_before = load_state(state_path)
    changed = absorb_operator_edits(state, state_path)
    assert changed == []
    assert load_state(state_path) == disk_before
    assert "now_row_set_at" not in disk_before


def test_hook_not_wired_in_attended_loop() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "liaison-tick.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    calls_in_loop = False
    calls_in_once = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_loop":
            src = ast.get_source_segment(script.read_text(encoding="utf-8"), node) or ""
            calls_in_loop = "maybe_bind_now_row" in src
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            src = ast.get_source_segment(script.read_text(encoding="utf-8"), node) or ""
            if "--once" in src:
                calls_in_once = "maybe_bind_now_row" in src
    assert not calls_in_loop
    assert not calls_in_once


def test_release_skipped_when_lanes_unobserved(state_path: Path) -> None:
    row = f"agent-bus:{LANE_A} · «G3 generate admitted»"
    digest = _digest(lanes=[], root_error="transport down")
    digest["policy"]["now_row"] = row
    state = _state(
        policy={"now_row": row},
        bind={"row": row, "lane_id": LANE_A, "tier": "attention"},
    )
    _write_state(state_path, state)
    before = json.dumps(load_state(state_path), sort_keys=True)
    with patch("bus_watch.now_row_bind.emit_now_row_released") as released:
        result = maybe_bind_now_row(digest, state, state_path, as_of=AS_OF)
    assert result["action"] == "none"
    assert result["source"] == "policy"
    assert json.dumps(load_state(state_path), sort_keys=True) == before
    assert state.get("now_row_bind") is not None
    released.assert_not_called()
