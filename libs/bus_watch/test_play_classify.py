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
    build_play_dispatch_body,
    classify_leftover,
    consult_reply_seat_empty,
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


def test_classify_hold_live_conductor_without_work_key() -> None:
    """Live conductor on the root whose todo does not match addressed slug is not HOLD."""
    digest = _digest()
    digest["policy"]["now_row"] = "todo:liaison-loop-tape-birth G4"
    digest["lanes"] = [
        {
            "id": "12032",
            "status": "active",
            "lifecycle": "admitted",
            "contract": "conductor",
            "slug": "cursor-sdk-generate-150cfca0",
            "last_subject": "cursor-sdk generate — 150cfca0",
        }
    ]
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_PLAY
    assert verdict["reason"] == "play_addressed_todo"


def test_play_dispatch_body_uses_resume_root_not_tape() -> None:
    body = build_play_dispatch_body(
        "12029",
        {"loop_thread": "12030", "max_hop_minutes": 60},
        todo_slug="liaison-loop-tape-birth",
    )
    assert body["dispatch_thread_id"] == "12029"
    assert body["source_ref"] == "todo:liaison-loop-tape-birth"
    assert "model" not in body


def test_play_dispatch_body_uses_successor_model() -> None:
    body = build_play_dispatch_body(
        "12557",
        {"successor_model": "cursor/grok-4.7", "max_hop_minutes": 60},
        todo_slug="cse-attachment-hop",
    )
    assert body["model"] == "cursor/grok-4.7"
    assert body["model_knobs"] == {"effort": "high", "fast": "false"}
    assert body["contract"] == "none"
    assert body["subject"] == "liaison-sdk-driver todo:cse-attachment-hop"
    assert "liaison-sdk-driver-turn.md" in body["prompt"]


def test_classify_holds_live_liaison() -> None:
    """A live liaison subject is held so the ticker does not admit a second one."""
    digest = _digest()
    digest["policy"]["now_row"] = "todo:alpha"
    digest["lanes"] = [
        {
            "id": "12660",
            "status": "active",
            "lifecycle": "admitted",
            "contract": "none",
            "last_subject": "liaison-sdk-driver todo:alpha",
        }
    ]
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["reason"] == "live_liaison"


def test_classify_hold_when_lanes_unobserved_and_todo_named() -> None:
    """Unsure live ⇒ hold, not play (wrong-direction: a second liaison)."""
    digest = {"policy": {"now_row": "todo:alpha"}, "lanes": None, "attention": []}
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["unsure_live"] is True


def test_hold_when_admit_has_no_worker_even_without_todo_on_lane() -> None:
    digest = _digest()
    digest["policy"]["now_row"] = "todo:steer"
    digest["policy"]["max_conductors"] = 2
    digest["lanes"] = [
        {"id": "12632", "lifecycle": "admitted", "status": "active", "turns": 1}
    ]
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["reason"] == "admit_not_worker"


def test_hold_when_open_conductors_meet_cap() -> None:
    digest = _digest()
    digest["policy"]["now_row"] = "todo:steer"
    digest["policy"]["max_conductors"] = 2
    digest["lanes"] = [
        {"id": "1", "lifecycle": "running", "status": "active"},
        {"id": "2", "lifecycle": "running", "status": "active"},
    ]
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["reason"] == "conductor_cap"


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


def _open_consult_reply_lane() -> dict[str, object]:
    """Bus thread still active; the latest turn is the consult reply."""
    return {
        "id": "12558",
        "turns": 5,
        "status": "active",
        "lifecycle": "active",
        "contract": "conductor",
        "lane_role": "sub_mission",
        "last_from": "web-anthropic",
        "last_subject": "cdp reply — 2b3801a2",
        "terminal": False,
    }


def test_consult_reply_on_terminal_conductor_open_thread_admits(
    monkeypatch,
) -> None:
    """Owed consult reply on an open thread admits the house conductor."""
    _free_lock(monkeypatch)
    monkeypatch.setattr(
        "bus_watch.spawn_wake.play_classify.consult_reply_seat_empty",
        lambda thread_id: thread_id == "12558",
    )
    digest = _digest(attention=[{"id": "12558", "unread": 1}])
    digest["policy"]["now_row"] = "todo:cse-attachment-hop"
    digest["lanes"] = [_open_consult_reply_lane()]
    out = tick_spawn_on_wake(digest, {}, "12557", dry_run=True)
    assert out["action"] == "would_spawn"
    body = out["body"]
    assert body["contract"] == "none"
    assert "liaison-sdk-driver" in body["subject"]
    assert body["source_ref"] == "todo:cse-attachment-hop"
    assert body["model"] == "cursor/grok-4.7"
    assert body["model_knobs"] == {"effort": "high", "fast": "false"}
    assert digest["lanes"][0]["seat_empty"] is True


def test_consult_reply_without_owed_row_does_not_hold_unmatched_conductor(
    monkeypatch,
) -> None:
    """Consult reply lane without matching todo does not HOLD (no live_conductor_on_root)."""
    _free_lock(monkeypatch)
    monkeypatch.setattr(
        "bus_watch.spawn_wake.play_classify.consult_reply_seat_empty",
        lambda _thread_id: False,
    )
    digest = _digest(attention=[{"id": "12558", "unread": 1}])
    digest["policy"]["now_row"] = "todo:cse-attachment-hop"
    digest["lanes"] = [_open_consult_reply_lane()]
    out = tick_spawn_on_wake(digest, {}, "12557", dry_run=True)
    assert out["action"] == "would_spawn"
    assert out["evaluation"]["leftover"]["leftover"] == LEFTOVER_PLAY
    assert "seat_empty" not in digest["lanes"][0]


def test_consult_reply_seat_empty_follows_owed_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "operator_hop_harvest.ledger.fetch_latest_terminal_conductor",
        lambda _thread_id: {
            "ledger_unreachable": False,
            "row": {
                "consult_pending_continue_owed": True,
                "record_json": {},
            },
        },
    )
    assert consult_reply_seat_empty("12558") is True


def test_dry_run_play_admits_liaison_once(
    monkeypatch,
) -> None:  # noqa: ANN001
    """Empty seat admits the liaison, not a house generate and not a conductor."""
    _free_lock(monkeypatch)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = "todo:alpha"
    digest["lanes"] = []
    out = tick_spawn_on_wake(digest, {}, "10479", dry_run=True)
    body = out["body"]
    assert body is not None
    assert body["contract"] == "none"
    assert body["subject"] == "liaison-sdk-driver todo:alpha"
    assert "Admit one conductor" in body["prompt"]
    assert body["source_ref"] == "todo:alpha"
    assert body["work_key"] == "todo:alpha"
    assert body["lane"] == "B"
    assert body["dispatch_thread_id"] == "10479"
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


def test_go_under_plants_aware_not_successor_contract(tmp_path, monkeypatch) -> None:  # noqa: ANN001
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


def test_go_under_sit_plants_forced_liaison_chain(tmp_path, monkeypatch) -> None:  # noqa: ANN001
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
