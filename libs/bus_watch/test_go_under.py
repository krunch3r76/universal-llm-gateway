"""``go under`` verb — state flip, seat release, ticker guarantee, handoff wake."""

from __future__ import annotations

import json
from pathlib import Path

from bus_watch.digest_budget import effective_policy
from bus_watch.go_under import go_under, under_line
from bus_watch.spawn_pending import handoff_wake, record_spawn_service


def _state() -> dict:
    return {
        "register": "attended",
        "ticks": 485,
        "policy": {
            "gear": "3-wake-on-attention",
            "ready": False,
            "successor_model": "cursor/composer-2.5",
        },
    }


def test_go_under_arms_and_frees_the_seat(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    holder = "ide:f0fbd8f2-305a-48e8-8c61-1004ecfff015"
    monkeypatch.setattr(
        "bus_watch.go_under.read_lock", lambda *_a, **_k: {"holder": holder}
    )
    released: list[tuple] = []
    state = _state()
    path = tmp_path / "liaison-10534.tick.json"
    result = go_under(
        "10534",
        state,
        state_path=path,
        holder=holder,
        stop_loops=lambda root: [4242],
        ensure=lambda root: {"alive": False, "started": True, "pid": 1},
        release=lambda h, **kw: released.append((h, kw)) or {"ok": True},
    )
    assert result["ok"] is True and result["armed"] is True
    assert state["register"] == "autonomous"
    assert state["policy"]["ready"] is True
    assert result["ready"] == {"before": False, "after": True}
    assert (
        state["handoff"]["seq"] == 1 and state["handoff"]["from_register"] == "attended"
    )
    assert released == [(holder, {"pid": None, "root_id": "10534"})]
    assert result["stopped_loops"] == [4242] and result["ticker"]["started"] is True
    assert json.loads(path.read_text())["handoff"]["seq"] == 1
    assert effective_policy(state)["ready"] is True
    assert handoff_wake(state) is True
    assert result["under_line"] == under_line("10534", effective_policy(state))
    assert "successor_model=cursor/composer-2.5" in result["under_line"]


def test_go_under_plants_gear_three_on_fresh_policy(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    """Go-under on a root that never set gear must enable wake + digest."""
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {
        "register": "attended",
        "policy": {"successor_model": "cdp/opus-5"},
    }
    result = go_under(
        "11667",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True, "started": False},
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["ok"] is True
    assert state["policy"]["gear"] == "3-wake-on-attention"
    assert state["policy"]["successor_seat"] == "cdp"
    eff = effective_policy(state)
    assert eff["wake_on_attention_only"] is True
    assert eff["post_digest"] is True
    assert eff["successor_model"] == "cdp/opus-5"
    assert eff["successor_seat"] == "cdp"


def test_go_under_leaves_sdk_holder_and_refuses_unbound_model(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.go_under.read_lock", lambda *_a, **_k: {"holder": "sdk:abc123"}
    )
    state = _state()
    state["policy"].pop("successor_model")
    result = go_under(
        "10479",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True, "started": False},
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["seat_release"]["reason"] == "not_ide_holder"
    assert result["ok"] is False and "successor_model_unset" in result["refused"]
    assert state["register"] == "autonomous"


def test_second_go_under_bumps_seq_and_spawn_latches_it(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {**_state(), "handoff": {"seq": 3}, "handoff_spawned_seq": 3}
    assert handoff_wake(state) is False
    go_under(
        "10534",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True},
        release=lambda *_a, **_k: {"ok": True},
    )
    assert state["handoff"]["seq"] == 4 and handoff_wake(state) is True
    state["pending_spawn"] = {"thread_id": "10601", "execution_id": "x"}
    record_spawn_service(
        state,
        [{"id": "10593", "turns": 2, "status": "closed", "lifecycle": "completed"}],
    )
    assert handoff_wake(state) is False
    assert state["served_closeouts"] == {"10593": 2}
    assert state["successor_threads"] == ["10601"]
