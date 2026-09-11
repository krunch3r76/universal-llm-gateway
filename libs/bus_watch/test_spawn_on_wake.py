"""Tests for gear-3 spawn predicate."""

from __future__ import annotations

import time

from bus_watch.liaison_digest import _fingerprint
from bus_watch.spawn_on_wake import (
    build_dispatch_body,
    evaluate_spawn_predicate,
    spawn_fingerprint,
)


def _digest(*, attention=None, checkpoint_due=False, turns=10):  # noqa: ANN001
    lanes = [{"id": "10496", "turns": 3, "status": "active", "lifecycle": "admitted"}]
    root = {"id": "10479", "turn_count": turns, "status": "active"}
    return {
        "root": root,
        "lanes": lanes,
        "attention": attention or [],
        "budget": {"checkpoint_due": checkpoint_due},
        "policy": {
            "ready": True,
            "max_hops_per_night": 8,
            "max_dispatches_per_night": 12,
            "spawn_grace_seconds": 900,
            "max_hop_minutes": 60,
            "wake_on_attention_only": True,
        },
    }


def test_predicate_refuses_live_seat_lock() -> None:
    lock = {"holder": "sdk:live", "expires_at": "2099-01-01T00:00:00Z"}
    ev = evaluate_spawn_predicate(
        _digest(attention=[{"id": "1", "unread": 1}]),
        {},
        lock=lock,
    )
    assert ev["spawn"] is False
    assert ev["clauses"]["seat_lock_free"] is False


def test_predicate_refuses_hop_cap(monkeypatch) -> None:  # noqa: ANN001
    night = "2026-09-11"
    monkeypatch.setattr("bus_watch.spawn_on_wake.current_night_id", lambda: night)
    lock = {"holder": None, "hops_by_night": {night: 8}, "hops": 8, "night_id": night}
    ev = evaluate_spawn_predicate(_digest(attention=[{"id": "1"}]), {}, lock=lock)
    assert ev["clauses"]["hops_under_cap"] is False


def test_predicate_refuses_not_ready() -> None:
    digest = _digest(attention=[{"id": "1"}])
    digest["policy"]["ready"] = False
    ev = evaluate_spawn_predicate(digest, {}, lock={"holder": None})
    assert ev["clauses"]["policy_ready"] is False


def test_predicate_refuses_pending_spawn() -> None:
    state = {"pending_spawn": {"execution_id": "e1", "thread_id": "t1"}}
    ev = evaluate_spawn_predicate(_digest(attention=[{"id": "1"}]), state, lock={"holder": None})
    assert ev["clauses"]["pending_spawn_terminal"] is False


def test_spawn_fingerprint_excludes_turn_count() -> None:
    """AC-12: spawn fingerprint ignores root.turn_count; digest fingerprint keeps it."""
    lanes = [{"id": "10496", "turns": 3, "status": "active", "lifecycle": "admitted"}]
    root_a = {"id": "10479", "turn_count": 10, "status": "active"}
    root_b = {"id": "10479", "turn_count": 11, "status": "active"}
    assert spawn_fingerprint(root_a, lanes) == spawn_fingerprint(root_b, lanes)
    assert _fingerprint(root_a, lanes) != _fingerprint(root_b, lanes)


def test_grace_and_fingerprint_clauses() -> None:
    digest = _digest(attention=[{"id": "1"}])
    state = {
        "last_spawn_fingerprint": spawn_fingerprint(digest["root"], digest["lanes"]),
        "last_spawn_at": time.time(),
    }
    ev = evaluate_spawn_predicate(digest, state, lock={"holder": None}, now=time.time())
    assert ev["clauses"]["fingerprint_changed"] is False
    assert ev["clauses"]["grace_elapsed"] is False


def test_dispatch_body_shape() -> None:
    body = build_dispatch_body(
        "10479",
        {
            "successor_model": "cursor/claude-opus-5",
            "successor_packet": "tmp/prompts/liaison-successor-10479.md",
            "max_hop_minutes": 60,
        },
    )
    assert body["op"] == "generate"
    assert body["work_key"] == "agent-bus:10479"
    assert body["timeout_seconds"] == 5400


def test_work_key_in_flight_refusal_recorded() -> None:
    from bus_watch.spawn_on_wake import fire_spawn

    state: dict = {}
    result = fire_spawn(
        "10479",
        {"successor_model": "cursor/claude-opus-5", "max_hop_minutes": 60},
        state,
        submit=lambda body: (
            {"error": {"code": "CURSOR_SOURCE_REF_IN_FLIGHT", "message": "busy"}},
            409,
        ),
    )
    assert result["status_code"] == 409
    assert result.get("quiet_refusal") is True
