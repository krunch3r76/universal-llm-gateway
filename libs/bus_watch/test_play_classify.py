"""Overnight leftover classifier — hold | play | sit (go-under play gap)."""

from __future__ import annotations

from bus_watch.go_under import go_under
from bus_watch.spawn_on_wake import tick_spawn_on_wake
from bus_watch.spawn_wake.play_classify import (
    LEFTOVER_HOLD,
    LEFTOVER_PLAY,
    LEFTOVER_SIT,
    MODE_AWARE,
    PLAY_HOLD,
    classify_leftover,
)
from bus_watch.test_spawn_on_wake import _digest


def _free_lock(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: {"holder": None}
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )


def test_classify_hold_when_live_conductor_owns_todo() -> None:
    digest = _digest()
    digest["policy"]["now_row"] = "Play todo:music-lexicon-accord G3"
    digest["lanes"] = [
        {
            "id": "11800",
            "status": "active",
            "lifecycle": "admitted",
            "contract": "conductor",
            "work_key": "todo:music-lexicon-accord",
        }
    ]
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["reason"] == PLAY_HOLD
    assert verdict["todo"] == "music-lexicon-accord"


def test_classify_hold_when_lanes_unobserved_and_todo_named() -> None:
    """Unsure live ⇒ hold, not play (wrong-direction: a second liaison)."""
    digest = {"policy": {"now_row": "todo:alpha"}, "lanes": None, "attention": []}
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["unsure_live"] is True


def test_classify_play_when_todo_named_and_no_owner() -> None:
    digest = _digest()
    digest["policy"]["now_row"] = "todo:alpha — thin scoreboard"
    digest["lanes"] = []
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_PLAY
    assert verdict["todo"] == "alpha"


def test_classify_sit_when_no_todo() -> None:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_SIT
    assert verdict["todo"] is None


def test_classify_sit_forced_even_when_todo_named() -> None:
    digest = _digest()
    digest["policy"]["now_row"] = "todo:alpha"
    digest["lanes"] = []
    verdict = classify_leftover(digest, {"play": {"mode": "sit"}})
    assert verdict["leftover"] == LEFTOVER_SIT
    assert verdict["reason"] == "sit_forced"


def test_dry_run_live_conductor_refuses_house_generate(
    monkeypatch,
) -> None:  # noqa: ANN001
    """AC1 — play_hold, no house generate."""
    _free_lock(monkeypatch)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = "todo:alpha"
    digest["lanes"] = [
        {
            "id": "11800",
            "turns": 4,
            "status": "active",
            "lifecycle": "admitted",
            "contract": "conductor",
            "work_key": "todo:alpha",
        }
    ]
    out = tick_spawn_on_wake(digest, {}, "10479", dry_run=True)
    assert out["action"] == "hold"
    assert out["refused"] == PLAY_HOLD
    assert out["body"] is None
    assert out["evaluation"]["leftover"]["leftover"] == LEFTOVER_HOLD


def test_dry_run_play_admits_conductor_not_liaison(
    monkeypatch,
) -> None:  # noqa: ANN001
    """AC2 — source_ref rematerialize, not night house generate."""
    _free_lock(monkeypatch)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = "todo:alpha"
    digest["lanes"] = []
    out = tick_spawn_on_wake(digest, {}, "10479", dry_run=True)
    body = out["body"]
    assert body is not None
    assert body["contract"] == "conductor"
    assert body["source_ref"] == "todo:alpha"
    assert body["work_key"] == "todo:alpha"
    assert body["lane"] == "B"
    assert not str(body.get("work_key")).startswith("agent-bus:")
    message = str(body.get("message") or body.get("prompt") or "")
    assert "WAKE — liaison headless successor" not in message
    assert out["evaluation"]["leftover"]["leftover"] == LEFTOVER_PLAY


def test_dry_run_sit_keeps_liaison_successor(monkeypatch) -> None:  # noqa: ANN001
    """AC3 — 10534 still exists as an explicit leftover class."""
    _free_lock(monkeypatch)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    out = tick_spawn_on_wake(digest, {}, "10479", dry_run=True)
    body = out["body"]
    assert body is not None
    assert body["contract"] == "none"
    assert str(body["work_key"]).startswith("agent-bus:10479:night-")
    assert "WAKE — liaison headless successor" in str(body.get("message") or "")
    assert out["evaluation"]["leftover"]["leftover"] == LEFTOVER_SIT


def test_go_under_plants_aware_not_successor_contract(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """AC4 — classifier default; play is not successor_contract=conductor."""
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {
        "register": "attended",
        "policy": {
            "gear": "3-wake-on-attention",
            "ready": False,
            "successor_model": "cursor/composer-2.5",
        },
    }
    result = go_under(
        "10534",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda _r: [],
        ensure=lambda _r: {"alive": True, "started": False},
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["play"]["mode"] == MODE_AWARE
    assert state["policy"]["play"] == MODE_AWARE
    assert state["policy"].get("successor_contract") != "conductor"
    assert "leftover=aware" in result["under_line"]


def test_go_under_sit_plants_forced_liaison_chain(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {
        "register": "attended",
        "policy": {"ready": False, "successor_model": "cursor/composer-2.5"},
    }
    result = go_under(
        "10534",
        state,
        state_path=tmp_path / "s.json",
        leftover_mode="sit",
        stop_loops=lambda _r: [],
        ensure=lambda _r: {"alive": True},
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["play"]["mode"] == "sit"
    assert state["policy"]["play"] == "sit"
    assert state["policy"].get("successor_contract") != "conductor"
