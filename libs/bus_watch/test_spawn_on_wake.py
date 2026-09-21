"""Tests for gear-3 spawn predicate."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from bus_watch.fable_lock import current_night_id
from bus_watch.liaison_digest import _fingerprint
from bus_watch.spawn_on_wake import (
    SUCCESSOR_MESSAGE_CAP,
    build_dispatch_body,
    build_successor_message,
    evaluate_spawn_predicate,
    fire_spawn,
    spawn_fingerprint,
    tick_spawn_on_wake,
)
from bus_watch.spawn_pending import digest_pending_is_terminal


def _digest(*, attention=None, checkpoint_due=False, turns=10, budget=None):  # noqa: ANN001
    lanes = [{"id": "10496", "turns": 3, "status": "active", "lifecycle": "admitted"}]
    root = {"id": "10479", "turn_count": turns, "status": "active"}
    return {
        "root": root,
        "lanes": lanes,
        "attention": attention or [],
        "checkpoint_due": checkpoint_due,
        "budget": budget or {"checkpoint_due": checkpoint_due},
        "policy": {
            "ready": True,
            "max_hops_per_night": 8,
            "max_dispatches_per_night": 12,
            "spawn_grace_seconds": 900,
            "max_hop_minutes": 60,
            "wake_on_attention_only": True,
            "gear": "3-wake-on-attention",
            "budget_max_age_s": 300,
            "successor_model": "cursor/grok-4.6",
        },
    }


def test_predicate_refuses_preset_sourced_successor_model() -> None:
    """A gear preset naming a premium model is not an operator choice (10534 2026-09-12)."""
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["successor_model"] = "cursor/claude-opus-5"
    digest["policy"]["successor_model_source"] = "gear_preset"
    ev = evaluate_spawn_predicate(digest, {}, lock={})
    assert ev["clauses"]["successor_model_bound"] is False
    assert ev["spawn"] is False


def test_predicate_refuses_unset_successor_model() -> None:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["successor_model"] = None
    ev = evaluate_spawn_predicate(digest, {}, lock={})
    assert ev["clauses"]["successor_model_bound"] is False


def test_dispatch_cap_is_opt_in() -> None:
    """Non-positive max_dispatches_per_night means no nightly ceiling (operator 2026-09-13)."""
    night = current_night_id()
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["max_dispatches_per_night"] = 0
    state = {"dispatches_tonight_by_night": {night: 9999}}
    ev = evaluate_spawn_predicate(digest, state, lock={})
    assert ev["clauses"]["dispatches_under_cap"] is True

    digest["policy"]["max_dispatches_per_night"] = 12
    capped = evaluate_spawn_predicate(digest, state, lock={})
    assert capped["clauses"]["dispatches_under_cap"] is False


def test_fire_spawn_refuses_unset_model_without_posting() -> None:
    posted: list[dict] = []
    result = fire_spawn(
        "10534",
        {"gear": "3-wake-on-attention", "max_hop_minutes": 60},
        {},
        submit=lambda body: posted.append(body) or ({}, 200),
    )
    assert result["refused"] == "successor_model_unset"
    assert posted == []


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
    monkeypatch.setattr(
        "bus_watch.spawn_wake.predicate.current_night_id", lambda: night
    )
    lock = {"holder": None, "hops_by_night": {night: 8}, "hops": 8, "night_id": night}
    ev = evaluate_spawn_predicate(_digest(attention=[{"id": "1"}]), {}, lock=lock)
    assert ev["clauses"]["hops_under_cap"] is False


def test_predicate_new_night_does_not_inherit_yesterdays_hops(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.spawn_wake.predicate.current_night_id", lambda: "2026-09-21"
    )
    lock = {
        "holder": None,
        "hops_by_night": {"2026-09-20": 8},
        "hops": 8,
        "night_id": "2026-09-20",
    }
    ev = evaluate_spawn_predicate(_digest(attention=[{"id": "1"}]), {}, lock=lock)
    assert ev["clauses"]["hops_under_cap"] is True
    assert ev["hops"] == 0


def test_predicate_zero_hop_cap_is_uncapped() -> None:
    digest = _digest(attention=[{"id": "1"}])
    digest["policy"]["max_hops_per_night"] = 0
    ev = evaluate_spawn_predicate(
        digest,
        {},
        lock={"holder": None, "hops_by_night": {"2026-09-11": 99}, "hops": 99},
    )
    assert ev["clauses"]["hops_under_cap"] is True


def test_budget_estimate_alone_is_not_spawn_signal() -> None:
    ev = evaluate_spawn_predicate(
        _digest(attention=[{"kind": "budget_estimate", "pct": 0.1}]),
        {},
        lock={"holder": None},
    )
    assert ev["clauses"]["spawn_signal"] is False
    assert ev["spawn"] is False


def test_unread_lane_still_spawn_signal_beside_budget_estimate() -> None:
    ev = evaluate_spawn_predicate(
        _digest(
            attention=[
                {"kind": "budget_estimate", "pct": 0.1},
                {"id": "1", "unread": 1},
            ]
        ),
        {},
        lock={"holder": None},
    )
    assert ev["clauses"]["spawn_signal"] is True


def test_predicate_refuses_not_ready() -> None:
    digest = _digest(attention=[{"id": "1"}])
    digest["policy"]["ready"] = False
    ev = evaluate_spawn_predicate(digest, {}, lock={"holder": None})
    assert ev["clauses"]["policy_ready"] is False


def test_predicate_refuses_pending_spawn() -> None:
    state = {"pending_spawn": {"execution_id": "e1", "thread_id": "t1"}}
    ev = evaluate_spawn_predicate(
        _digest(attention=[{"id": "1"}]), state, lock={"holder": None}
    )
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
    assert ev["clauses"]["fingerprint_changed"] is True
    assert ev["clauses"]["grace_elapsed"] is False
    assert ev["spawn"] is False

    quiet = _digest(attention=[])
    quiet_state = {
        "last_spawn_fingerprint": spawn_fingerprint(quiet["root"], quiet["lanes"]),
        "last_spawn_at": time.time(),
    }
    quiet_ev = evaluate_spawn_predicate(
        quiet, quiet_state, lock={"holder": None}, now=time.time()
    )
    assert quiet_ev["clauses"]["fingerprint_changed"] is False
    assert quiet_ev["clauses"]["grace_elapsed"] is False


def test_dispatch_body_message_not_packet() -> None:
    body = build_dispatch_body(
        "10479",
        {
            "successor_model": "cursor/claude-opus-5",
            "max_hop_minutes": 60,
            "gear": "3-wake-on-attention",
        },
        successor_context={
            "gear": "3-wake-on-attention",
            "row": "Settled · Live · Next",
            "tip_turn": 42,
            "tip_checkpoint_turn": 40,
            "spawn_signal_sources": ["actionable_attention"],
        },
    )
    assert body["op"] == "generate"
    assert body["contract"] == "none"
    assert body["contract"] not in {"implement", "pure-mechanical"}
    assert "packet_path" not in body
    assert "message" in body
    message = body["message"]
    assert len(message.encode("utf-8")) <= SUCCESSOR_MESSAGE_CAP
    assert "open_line=" not in message
    for token in (
        "resume 10479",
        'dispatch(tool="continuity"',
        "agent_bus_read(thread_get",
        "gear:",
        "row=",
        "tip turn #",
        "tip CHECKPOINT #",
        "spawn_signal=",
        "contract: none",
        "LOAD the liaison skill",
        "hop only if hop_qualifies",
        "STAY",
        "runbook:bus-consult-watcher",
        "§ Peer-house",
    ):
        assert token in message
    # Per-night key: GIW's remint cap counts admits per work_key (a:33139).
    assert body["work_key"] == f"agent-bus:10479:night-{current_night_id()}"
    assert body["timeout_seconds"] == 5400
    assert "tags" not in body


def test_wire_submit_body_maps_message_and_drops_tags() -> None:
    from bus_watch.spawn_on_wake import _wire_submit_body

    wired = _wire_submit_body(
        {
            "op": "generate",
            "message": "resume 10534",
            "tags": ["liaison-headless"],
        }
    )
    assert wired["prompt"] == "resume 10534"
    assert "message" not in wired
    assert "tags" not in wired


def test_successor_message_sheds_when_over_cap() -> None:
    text = build_successor_message(
        "10479",
        gear="3-wake-on-attention",
        row="x" * 3000,
        tip_turn=1,
        tip_checkpoint_turn=1,
    )
    assert len(text.encode("utf-8")) <= 2048
    assert "..." in text


def _default_successor() -> str:
    return build_successor_message(
        "10479",
        gear="3-wake-on-attention",
        row="Settled · Live · Next",
        tip_turn=42,
        tip_checkpoint_turn=40,
    )


def test_successor_message_is_doorbell_shaped() -> None:
    text = _default_successor()
    lines = text.splitlines()
    assert lines[0] == "resume 10479"
    assert lines[1] == ""
    for prefix in (
        "WAKE —",
        "duty:",
        "disclosure:",
        "objective:",
        "addresses:",
        "frame:",
        "echo:",
    ):
        assert sum(1 for ln in text.splitlines() if ln.startswith(prefix)) == 1
    assert text.count("Use the liaison skill.") == 1


def test_successor_message_ring_and_extra_addresses() -> None:
    text = build_successor_message(
        "10479",
        gear="3-wake-on-attention",
        row="Settled · Live · Next",
        tip_turn=42,
        tip_checkpoint_turn=40,
        ring="10532",
        extra_addresses=(
            "cortex://notes/system/threads/10479-charter-scoreboard.md#Loop",
        ),
    )
    assert "thread=10532" in text
    assert 'subject="ORIENTED 10479"' in text
    assert "agent-bus:10532 (echo)" in text
    assert "agent-bus:10479 (echo)" not in text
    assert (
        "fs(op=md_read, path=cortex://notes/system/threads/10479-charter-scoreboard.md, section=Loop)"
        in text
    )


def test_successor_message_forbidden_content_absent() -> None:
    text = _default_successor()
    lower = text.lower()
    assert "you are" not in lower
    assert not any(line.startswith("NOW") for line in text.splitlines())
    for banned in ("reasoning-posture", "ulg-for-llms", "hypothesize-simulate"):
        assert banned not in text


def test_successor_message_determinism_and_headroom() -> None:
    first = _default_successor()
    second = _default_successor()
    assert first == second
    encoded_len = len(first.encode("utf-8"))
    assert encoded_len <= SUCCESSOR_MESSAGE_CAP - 200


def test_dispatch_body_threads_ring_and_extras() -> None:
    body = build_dispatch_body(
        "10479",
        {
            "successor_model": "cursor/claude-opus-5",
            "max_hop_minutes": 60,
            "gear": "3-wake-on-attention",
            "wake_ring": "10532",
            "successor_extra_addresses": [
                "cortex://notes/system/threads/10479-charter-scoreboard.md#Loop"
            ],
        },
        successor_context={
            "gear": "3-wake-on-attention",
            "row": "Settled · Live · Next",
            "tip_turn": 42,
            "tip_checkpoint_turn": 40,
            "spawn_signal_sources": ["actionable_attention"],
        },
    )
    message = body["message"]
    assert "agent-bus:10532 (echo)" in message
    assert (
        "fs(op=md_read, path=cortex://notes/system/threads/10479-charter-scoreboard.md, section=Loop)"
        in message
    )


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


def test_context_budget_fresh_spawn() -> None:
    now = time.time()
    digest = _digest(
        budget={
            "stop_class": "CONTEXT_BUDGET",
            "as_of": "2099-01-01T00:00:00Z",
            "epoch": "dispatch-live",
        },
    )
    lock = {"holder": "sdk:dispatch-live"}
    ev = evaluate_spawn_predicate(digest, {}, lock=lock, now=now)
    assert ev["spawn"] is True
    assert ev["context_budget"]["fresh"] is True


def test_context_budget_stale_no_spawn() -> None:
    stale = time.time() - 600
    digest = _digest(
        budget={
            "stop_class": "CONTEXT_BUDGET",
            "as_of": "2020-01-01T00:00:00Z",
            "epoch": "dispatch-live",
        },
    )
    lock = {"holder": "sdk:dispatch-live"}
    ev = evaluate_spawn_predicate(digest, {}, lock=lock, now=stale)
    assert ev["spawn"] is False
    assert ev["context_budget"]["fresh"] is False
    assert ev["context_budget"]["reason"] == "budget_stale"


def test_context_budget_epoch_mismatch_no_spawn() -> None:
    digest = _digest(
        budget={
            "stop_class": "CONTEXT_BUDGET",
            "as_of": "2099-01-01T00:00:00Z",
            "epoch": "dispatch-other",
        },
    )
    lock = {"holder": "sdk:dispatch-live"}
    ev = evaluate_spawn_predicate(digest, {}, lock=lock, now=time.time())
    assert ev["spawn"] is False
    assert ev["context_budget"]["fresh"] is False
    assert ev["context_budget"]["reason"] == "epoch_mismatch"


_CLOSED_WORK = {
    "id": "10593",
    "unread": 2,
    "turns": 2,
    "status": "closed",
    "lifecycle": "completed",
    "last_subject": "cursor-sdk CLOSEOUT 2dee4a448315-c155f848 contract=implement",
    "unread_turns": [
        {
            "turn_number": 1,
            "from": "web-anthropic",
            "subject": "IMPLEMENT",
            "body": "TYPE: DIRECTIVE\n\nDo the thing.",
        },
        {
            "turn_number": 2,
            "from": "cursor-sdk",
            "subject": "cursor-sdk CLOSEOUT 2dee4a448315 contract=implement",
            "body": '{"status":"complete"}',
        },
    ],
}
_CLOSED_SUCCESSOR = {
    "id": "10583",
    "unread": 5,
    "turns": 6,
    "status": "closed",
    "lifecycle": "completed",
    "last_subject": "cursor-sdk CLOSEOUT e8c9bc509ca0 contract=none caller=liaison-ticker",
}


def test_closed_unread_work_lane_wakes_once() -> None:
    """A worker's closeout is the wake (10534 #167); relay turns after latch do not re-arm."""
    first = evaluate_spawn_predicate(
        _digest(attention=[_CLOSED_WORK]), {}, lock={"holder": None}
    )
    assert first["clauses"]["spawn_signal"] is True
    served = evaluate_spawn_predicate(
        _digest(attention=[_CLOSED_WORK]),
        {"served_closeouts": {"10593": 2}},
        lock={"holder": None},
    )
    assert served["clauses"]["spawn_signal"] is False
    relay_only = {
        **_CLOSED_WORK,
        "turns": 4,
        "unread": 2,
        "unread_turns": [
            {
                "turn_number": 3,
                "from": "cursor-auto",
                "subject": "status:partial:capture — relay",
                "body": "TYPE: CLOSEOUT\nstatus: partial:capture\n",
            },
            {
                "turn_number": 4,
                "from": "cursor-auto",
                "subject": "WAKE — closeout relayed · auto-deadbeef",
                "body": (
                    "TYPE: WAKE\n"
                    "dispatch_id: auto-deadbeef\n"
                    "Unread ping for an absent operator waiter after CLOSEOUT relay; "
                    "not a new DIRECTIVE."
                ),
            },
        ],
    }
    assert (
        evaluate_spawn_predicate(
            _digest(attention=[relay_only]),
            {"served_closeouts": {"10593": 2}},
            lock={"holder": None},
        )["clauses"]["spawn_signal"]
        is False
    )
    new_directive = {
        **_CLOSED_WORK,
        "turns": 5,
        "unread": 3,
        "unread_turns": relay_only["unread_turns"]
        + [
            {
                "turn_number": 5,
                "from": "web-anthropic",
                "subject": "IMPLEMENT — follow-up",
                "body": "TYPE: DIRECTIVE\n\nNew work after closeout.",
            }
        ],
    }
    assert (
        evaluate_spawn_predicate(
            _digest(attention=[new_directive]),
            {"served_closeouts": {"10593": 2}},
            lock={"holder": None},
        )["clauses"]["spawn_signal"]
        is True
    )


def test_successor_closeout_never_wakes_next_successor() -> None:
    by_mark = evaluate_spawn_predicate(
        _digest(attention=[_CLOSED_SUCCESSOR]), {}, lock={"holder": None}
    )
    assert by_mark["clauses"]["spawn_signal"] is False
    plain = {**_CLOSED_WORK, "id": "10600", "last_subject": "CLOSEOUT"}
    by_record = evaluate_spawn_predicate(
        _digest(attention=[plain]),
        {"successor_threads": ["10600"]},
        lock={"holder": None},
    )
    assert by_record["clauses"]["spawn_signal"] is False
    read = evaluate_spawn_predicate(
        _digest(attention=[{**_CLOSED_WORK, "unread": 0}]), {}, lock={"holder": None}
    )
    assert read["clauses"]["spawn_signal"] is False


def test_handoff_wakes_once_per_seq() -> None:
    state = {"handoff": {"seq": 1, "requested_at": "2026-09-13T06:30:00Z"}}
    armed = evaluate_spawn_predicate(_digest(), state, lock={"holder": None})
    assert armed["clauses"]["spawn_signal"] is True
    assert armed["handoff"]["seq"] == 1
    latched = evaluate_spawn_predicate(
        _digest(), {**state, "handoff_spawned_seq": 1}, lock={"holder": None}
    )
    assert latched["clauses"]["spawn_signal"] is False
    assert "handoff" not in latched


def test_idle_ide_holder_forfeits_only_when_autonomous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bus_watch.spawn_pending.ide_holder_idle_s", lambda *_a, **_k: 3600.0
    )
    monkeypatch.setattr(
        "bus_watch.spawn_pending.ide_transcript_probe_resolved", lambda *_a, **_k: True
    )
    lock = {
        "holder": "ide:ccd52168-8bf7-4080-bc05-75fe45af1507",
        "claimed_at": "2099-01-01T00:00:00Z",
        "expires_at": "2099-01-01T01:30:00Z",
    }
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["register"] = "attended"
    held = evaluate_spawn_predicate(digest, {}, lock=lock)
    assert held["clauses"]["seat_lock_free"] is False
    digest["register"] = "autonomous"
    forfeit = evaluate_spawn_predicate(digest, {}, lock=lock)
    assert forfeit["clauses"]["seat_lock_free"] is True
    assert forfeit["idle_ide_forfeit"]["holder"] == lock["holder"]
    digest["policy"]["ide_idle_forfeit_s"] = 7200
    kept = evaluate_spawn_predicate(digest, {}, lock=lock)
    assert kept["clauses"]["seat_lock_free"] is False


def test_ticker_reaps_dead_sdk_holder_before_evaluating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10599 closed without --release; its sdk: lease must not hold the house."""
    locks = iter(
        [
            {
                "holder": "sdk:a1ae2160-caa7-4571-a248-d8f1a545bc7e",
                "claimed_at": "2099-01-01T00:00:00Z",
                "expires_at": "2099-01-01T01:30:00Z",
            },
            {"holder": None},
        ]
    )
    released: list[str] = []
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: next(locks)
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.release_fable_lock",
        lambda holder, **_k: released.append(holder) or {"ok": True},
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )
    state = {
        "pending_spawn": {
            "execution_id": "a1ae2160-caa7-4571-a248-d8f1a545bc7e",
            "thread_id": "10599",
        }
    }
    out = tick_spawn_on_wake(
        _digest(attention=[{"id": "10589", "unread": 1}]),
        state,
        "10479",
        dry_run=True,
        is_terminal=lambda _p: True,
    )
    assert released == ["sdk:a1ae2160-caa7-4571-a248-d8f1a545bc7e"]
    assert "pending_spawn" not in state
    assert out["evaluation"]["reaped_sdk_holder"] == released[0]
    assert out["evaluation"]["clauses"]["seat_lock_free"] is True


def test_checkpoint_due_wakes_once_per_cp_tick() -> None:
    digest = _digest(attention=[_CLOSED_SUCCESSOR], checkpoint_due=True)
    first = evaluate_spawn_predicate(digest, {}, lock={"holder": None})
    assert first["clauses"]["spawn_signal"] is True
    latched = evaluate_spawn_predicate(
        digest,
        {"last_cp_tick": 72, "checkpoint_due_spawned_tick": 72},
        lock={"holder": None},
    )
    assert latched["clauses"]["spawn_signal"] is False


def test_pending_clears_when_digest_shows_closed_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: {"holder": None}
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )
    digest = _digest(attention=[], checkpoint_due=False)
    digest["lanes"] = [
        {
            "id": "10579",
            "turns": 10,
            "status": "closed",
            "lifecycle": "completed",
            "unread": 2,
        }
    ]
    digest["attention"] = [
        {
            "id": "10579",
            "turns": 10,
            "unread": 2,
            "status": "closed",
            "lifecycle": "completed",
            "unread_turns": [
                {
                    "turn_number": 9,
                    "from": "cursor-auto",
                    "subject": "status:partial:capture — relay",
                    "body": "TYPE: CLOSEOUT\nstatus: partial:capture\n",
                },
                {
                    "turn_number": 10,
                    "from": "cursor-auto",
                    "subject": "WAKE — closeout relayed",
                    "body": "TYPE: WAKE\n",
                },
            ],
        }
    ]
    state = {
        "pending_spawn": {
            "execution_id": "b60067e7",
            "thread_id": "10579",
            "spawned_at": "2026-09-12T16:54:26Z",
        }
    }
    result = tick_spawn_on_wake(digest, state, "10534", dry_run=True)
    assert "pending_spawn" not in state
    assert result["evaluation"]["clauses"]["pending_spawn_terminal"] is True
    assert result["action"] == "hold"


def test_root_pending_not_terminal_at_max_hop_without_closeout() -> None:
    """a:34257 specimen — ROOT thread_id must not release at max_hop_minutes."""
    pending = {
        "execution_id": "585b780e-685e-402b-aee4-2abae834c1a3",
        "thread_id": "10479",
        "spawned_at": "2026-09-16T10:00:22Z",
    }
    digest = {
        "root": {"id": "10479", "turns": 2634, "recent_turns": []},
        "lanes": [],
        "attention": [{"kind": "budget_estimate"}],
        "policy": {"max_hop_minutes": 30, "pending_stale_backstop_minutes": 180},
    }
    spawned = datetime.fromisoformat("2026-09-16T10:00:22+00:00").timestamp()
    checker = digest_pending_is_terminal(
        digest,
        now=spawned + 1900,
        execution_gone_fn=lambda _p: False,
    )
    assert checker(pending) is False
    assert checker.last_reason == "unresolved_root_pending"


def test_root_pending_terminal_on_successor_closeout_on_root() -> None:
    pending = {
        "execution_id": "585b780e-685e-402b-aee4-2abae834c1a3",
        "thread_id": "10479",
        "spawned_at": "2026-09-16T10:00:22Z",
    }
    digest = {
        "root": {
            "id": "10479",
            "turns": 2635,
            "recent_turns": [
                {
                    "turn_number": 2635,
                    "from": "cdp",
                    "subject": "CHECKPOINT 10479",
                    "body": "TYPE: CHECKPOINT\n585b780e-685e-402b-aee4-2abae834c1a3",
                    "created_at": "2026-09-16T10:31:00Z",
                }
            ],
        },
        "lanes": [],
        "attention": [],
        "policy": {"max_hop_minutes": 30, "pending_stale_backstop_minutes": 180},
    }
    spawned = datetime.fromisoformat("2026-09-16T10:00:22+00:00").timestamp()
    checker = digest_pending_is_terminal(digest, now=spawned + 1900)
    assert checker(pending) is True
    assert checker.last_reason == "lifecycle"


def test_root_pending_terminal_when_uuid_past_compact_body() -> None:
    """11667-class: CHECKPOINT names pending id after body[:400] truncation."""
    from bus_watch.spawn_pending import compact_root_turn_summaries

    execution_id = "bf19310b-1976-4500-a3c5-7c325afcc7e7"
    padding = "x" * 420
    raw_turn = {
        "turn_number": 104,
        "from": "cursor",
        "subject": "CHECKPOINT 11667",
        "body": f"{padding}{execution_id}\nTYPE: CHECKPOINT\n",
        "created_at": "2026-09-18T16:30:00Z",
    }
    compact = compact_root_turn_summaries([raw_turn])[0]
    assert execution_id not in compact["body"]
    assert execution_id in compact["execution_ids"]

    pending = {
        "execution_id": execution_id,
        "thread_id": "11667",
        "spawned_at": "2026-09-18T16:15:00Z",
    }
    digest = {
        "root": {"id": "11667", "turns": 104, "recent_turns": [compact]},
        "lanes": [],
        "attention": [],
        "policy": {"pending_stale_backstop_minutes": 180},
    }
    spawned = datetime.fromisoformat("2026-09-18T16:15:00+00:00").timestamp()
    checker = digest_pending_is_terminal(
        digest,
        now=spawned + 60,
        execution_gone_fn=lambda _p: False,
    )
    assert checker(pending) is True
    assert checker.last_reason == "lifecycle"


def test_root_pending_execution_gone_releases_before_backstop() -> None:
    pending = {
        "execution_id": "585b780e-685e-402b-aee4-2abae834c1a3",
        "thread_id": "10479",
        "spawned_at": "2026-09-16T10:00:22Z",
    }
    digest = {
        "root": {"id": "10479", "turns": 2634, "recent_turns": []},
        "lanes": [],
        "attention": [],
        "policy": {"pending_stale_backstop_minutes": 180},
    }
    spawned = datetime.fromisoformat("2026-09-16T10:00:22+00:00").timestamp()
    checker = digest_pending_is_terminal(
        digest,
        now=spawned + 60,
        execution_gone_fn=lambda _p: True,
    )
    assert checker(pending) is True
    assert checker.last_reason == "execution_gone"


def test_root_pending_execution_gone_probe_error_stays_unresolved() -> None:
    pending = {
        "execution_id": "585b780e-685e-402b-aee4-2abae834c1a3",
        "thread_id": "10479",
        "spawned_at": "2026-09-16T10:00:22Z",
    }
    digest = {
        "root": {"id": "10479", "turns": 2634, "recent_turns": []},
        "lanes": [],
        "attention": [],
        "policy": {"pending_stale_backstop_minutes": 180},
    }
    spawned = datetime.fromisoformat("2026-09-16T10:00:22+00:00").timestamp()
    checker = digest_pending_is_terminal(
        digest,
        now=spawned + 60,
        execution_gone_fn=lambda _p: False,
    )
    assert checker(pending) is False
    assert checker.last_reason == "unresolved_root_pending"


def test_stale_pending_without_lane_is_terminal() -> None:
    digest = _digest(attention=[{"id": "1"}])
    pending = {
        "execution_id": "e1",
        "thread_id": "10579",
        "spawned_at": "2026-09-12T10:00:00Z",
    }
    ev = evaluate_spawn_predicate(
        digest,
        {"pending_spawn": pending},
        lock={"holder": None},
        now=datetime.fromisoformat("2026-09-12T20:00:00+00:00").timestamp(),
    )
    assert ev["clauses"]["pending_spawn_terminal"] is True


def test_remint_cap_refusal_is_a_wall_for_the_night(monkeypatch) -> None:  # noqa: ANN001
    """10479 2026-09-13: 169 refused spawns in six hours. One REMINT_CAP 409 latches
    the night, pages once, and the predicate holds until the night rolls."""
    from bus_watch.spawn_wake import fire as spawn_fire

    pages: list[tuple] = []
    monkeypatch.setattr(spawn_fire, "page_liaison", lambda *a: pages.append(a))
    refusal = (
        {
            "error": {
                "code": "CURSOR_WORK_KEY_REMINT_CAP",
                "message": "work_key 'agent-bus:10479:night-x' remint seq 9 exceeds cap 8",
            }
        },
        409,
    )
    state: dict = {}
    policy = {"gear": "3-wake-on-attention", "successor_model": "cursor/grok-4.6"}
    first = fire_spawn("10479", policy, state, submit=lambda body: refusal)
    assert first["quiet_refusal"] is True
    assert first["remint_cap_wall"]["night_id"] == current_night_id()
    assert len(pages) == 1
    second = fire_spawn("10479", policy, state, submit=lambda body: refusal)
    assert "remint_cap_wall" not in second and len(pages) == 1
    ev = evaluate_spawn_predicate(
        _digest(attention=[{"id": "1", "unread": 1}]), state, lock={}
    )
    assert ev["clauses"]["remint_cap_clear"] is False and ev["spawn"] is False
    rolled = {"remint_cap_wall": {"night_id": "1999-01-01"}}
    assert evaluate_spawn_predicate(_digest(), rolled, lock={})["clauses"][
        "remint_cap_clear"
    ]


def test_successor_wake_shell_seat_names_seat_and_emits_watcher() -> None:
    text = build_successor_message(
        "10479",
        gear="3-wake-on-attention",
        row="Settled · Live · Next",
        seat="cursor-sdk",
        tip_turn=42,
        tip_checkpoint_turn=40,
    )
    assert "seat cursor-sdk" in text
    assert "seat: cursor-sdk" in text
    assert "runbook:bus-consult-watcher" in text


def test_successor_wake_cdp_seat_names_seat_and_omits_watcher() -> None:
    text = build_successor_message(
        "10479",
        gear="4-cdp-liaison",
        row="Settled · Live · Next",
        seat="cdp",
        tip_turn=42,
        tip_checkpoint_turn=40,
    )
    assert "seat cdp" in text
    assert "seat: cdp" in text
    assert "runbook:bus-consult-watcher" not in text


def test_successor_wake_web_anthropic_seat_names_seat_and_omits_watcher() -> None:
    text = build_successor_message(
        "10479",
        gear="3-wake-on-attention",
        row="Settled · Live · Next",
        seat="web-anthropic",
        tip_turn=42,
        tip_checkpoint_turn=40,
    )
    assert "seat web-anthropic" in text
    assert "seat: web-anthropic" in text
    assert "runbook:bus-consult-watcher" not in text


def test_dispatch_body_passes_resolved_seat_into_message() -> None:
    body = build_dispatch_body(
        "10479",
        {
            "successor_model": "cdp/opus-5",
            "successor_seat": "cdp",
            "successor_contract": "conductor",
            "max_hop_minutes": 60,
            "gear": "4-cdp-liaison",
        },
        successor_context={
            "gear": "4-cdp-liaison",
            "row": "Settled · Live · Next",
            "tip_turn": 42,
            "tip_checkpoint_turn": 40,
            "spawn_signal_sources": ["actionable_attention"],
        },
    )
    assert body["seat"] == "cdp"
    assert body["contract"] == "conductor"
    assert "seat cdp" in body["message"]
    assert "— contract: conductor." in body["message"]
    assert "runbook:bus-consult-watcher" not in body["message"]


def test_dispatch_body_contract_defaults_none_when_absent() -> None:
    """AC1.2 — absent successor_contract stays none on the wire."""
    body = build_dispatch_body(
        "10479",
        {"successor_model": "cursor/claude-fable-5-1", "max_hop_minutes": 60},
    )
    assert body["contract"] == "none"


def test_dispatch_body_grok_successor_pins_fast() -> None:
    """Grok liaison hops pin Fast — omit-path alone is Standard (a:35522)."""
    body = build_dispatch_body(
        "10479",
        {"successor_model": "cursor/grok-4.6", "max_hop_minutes": 60},
    )
    assert body["model_knobs"] == {"fast": "true"}


def test_dispatch_body_successor_model_knobs_override_grok_fast() -> None:
    body = build_dispatch_body(
        "10479",
        {
            "successor_model": "cursor/grok-4.6",
            "successor_model_knobs": {"fast": "false"},
            "max_hop_minutes": 60,
        },
    )
    assert body["model_knobs"] == {"fast": "false"}


def test_dispatch_body_non_grok_omits_model_knobs() -> None:
    body = build_dispatch_body(
        "10479",
        {"successor_model": "cursor/claude-opus-5", "max_hop_minutes": 60},
    )
    assert "model_knobs" not in body


def test_dispatch_body_contract_conductor_from_policy() -> None:
    """AC1.2 — policy successor_contract binds the generate payload."""
    body = build_dispatch_body(
        "10479",
        {
            "successor_contract": "conductor",
            "successor_model": "cdp/opus-5",
            "max_hop_minutes": 60,
        },
    )
    assert body["contract"] == "conductor"


def test_abandoned_lane_does_not_wake() -> None:
    from bus_watch.spawn_pending import actionable_attention

    orphan = {
        "id": "10955",
        "unread": 2,
        "status": "active",
        "lifecycle": "abandoned",
        "last_subject": "Dispatch orphaned — worker terminated before completion",
    }
    assert actionable_attention([orphan], state={}) == []


def test_relay_ping_specimen_11440_latched_at_3_stays_suppressed() -> None:
    """AC3 — live 11440 shape: closeout at 3, two relay turns, served mark 3 → []."""
    from bus_watch.spawn_pending import actionable_attention

    lane = {
        "id": "11440",
        "unread": 2,
        "turns": 5,
        "status": "closed",
        "lifecycle": "completed",
        "unread_turns": [
            {
                "turn_number": 4,
                "from": "cursor-auto",
                "subject": "status:partial:capture — IMPLEMENT",
                "body": "TYPE: CLOSEOUT\nstatus: partial:capture\n",
            },
            {
                "turn_number": 5,
                "from": "cursor-auto",
                "subject": "WAKE — closeout relayed · auto-af1abe9399c5",
                "body": (
                    "TYPE: WAKE\n"
                    "dispatch_id: auto-af1abe9399c5\n"
                    "Unread ping for an absent operator waiter after CLOSEOUT relay; "
                    "not a new DIRECTIVE."
                ),
            },
        ],
    }
    assert actionable_attention([lane], state={"served_closeouts": {"11440": 3}}) == []


def test_relay_ping_specimen_11446_progress_plus_relay_stays_suppressed() -> None:
    """AC3 — live 11446 shape: progress heartbeat + relay + WAKE after latch at 3."""
    from bus_watch.spawn_pending import actionable_attention

    lane = {
        "id": "11446",
        "unread": 3,
        "turns": 6,
        "status": "closed",
        "lifecycle": "completed",
        "unread_turns": [
            {
                "turn_number": 4,
                "from": "cursor-auto",
                "subject": "progress — elapsed 301s",
                "body": (
                    '{\n  "summary": "Still running — 5.0 min elapsed, no terminal yet."\n}'
                ),
            },
            {
                "turn_number": 5,
                "from": "cursor-auto",
                "subject": "status:partial:capture — IMPLEMENT",
                "body": "TYPE: CLOSEOUT\nstatus: partial:capture\n",
            },
            {
                "turn_number": 6,
                "from": "cursor-auto",
                "subject": "WAKE — closeout relayed · auto-de4f3f9e8b3a",
                "body": "TYPE: WAKE\n",
            },
        ],
    }
    assert actionable_attention([lane], state={"served_closeouts": {"11446": 3}}) == []


def test_actionable_kind_keys_on_from_and_type_line_only() -> None:
    """AC5 — classifier ignores model prose; only from + TYPE: / progress markers."""
    from bus_watch.spawn_pending import actionable_kind

    relay = {
        "from": "cursor-auto",
        "subject": "WAKE — closeout relayed · auto-x",
        "body": (
            "TYPE: WAKE\n"
            "This prose would look like TYPE: DIRECTIVE if we scanned the whole body."
        ),
    }
    assert actionable_kind(relay) is False
    prose_directive = {
        "from": "cursor-auto",
        "subject": "looks like a directive",
        "body": "TYPE: CLOSEOUT\nThe operator wrote TYPE: DIRECTIVE in the essay below.",
    }
    assert actionable_kind(prose_directive) is False
    assert (
        actionable_kind(
            {
                "from": "web-anthropic",
                "subject": "IMPLEMENT",
                "body": "TYPE: DIRECTIVE\n\nReal commission.",
            }
        )
        is True
    )


def test_actionable_kind_unknown_turn_fails_closed() -> None:
    """AC5 — unrecognized kind is actionable, not suppressed."""
    from bus_watch.spawn_pending import actionable_kind

    assert (
        actionable_kind(
            {
                "from": "cursor-auto",
                "subject": "status:admitted",
                "body": "TYPE: BRIEFING\n\nEpisode open.",
            }
        )
        is True
    )


def test_successor_context_separates_tip_turn_from_checkpoint() -> None:
    from bus_watch.spawn_on_wake import successor_context_from_digest

    ctx = successor_context_from_digest(
        {
            "root": {"id": "10479", "turns": 2669, "tip_checkpoint_turn": 2661},
            "policy": {"gear": "3-wake-on-attention"},
            "spawn_signal_sources": ["actionable_attention"],
        }
    )
    assert ctx["tip_turn"] == 2669
    assert ctx["tip_checkpoint_turn"] == 2661
    assert ctx["tip_turn"] != ctx["tip_checkpoint_turn"]


def test_evaluate_spawn_predicate_reports_signal_sources() -> None:
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    ev = evaluate_spawn_predicate(digest, {}, lock={"holder": None})
    assert ev["spawn_signal_sources"] == ["actionable_attention"]
    assert ev["clauses"]["spawn_signal"] is True


_LIVE_LANE = {
    "id": "11570",
    "unread": 1,
    "turns": 5,
    "status": "active",
    "lifecycle": "admitted",
}


def test_live_lane_latched_at_turns_not_actionable_second_tick() -> None:
    """AC4(i) — same ``turns`` mark must not re-arm live unread attention."""
    from bus_watch.spawn_pending import actionable_attention

    assert actionable_attention([_LIVE_LANE], state={}) == [_LIVE_LANE]
    assert (
        actionable_attention([_LIVE_LANE], state={"served_lanes": {"11570": 5}}) == []
    )


def test_live_lane_turns_increment_actionable_again() -> None:
    """AC4(ii) — incremented ``turns`` re-arms after ``served_lanes`` latch."""
    from bus_watch.spawn_pending import actionable_attention

    bumped = {**_LIVE_LANE, "turns": 6}
    assert actionable_attention([bumped], state={"served_lanes": {"11570": 5}}) == [
        bumped
    ]


def test_frozen_fingerprint_no_unlatched_source_no_spawn() -> None:
    """AC4(iii) — unchanged fingerprint with all sources latched must not spawn."""
    digest = _digest(attention=[])
    fp = spawn_fingerprint(digest["root"], digest["lanes"])
    state = {"last_spawn_fingerprint": fp, "last_spawn_at": 0.0}
    ev = evaluate_spawn_predicate(digest, state, lock={"holder": None}, now=time.time())
    assert ev["clauses"]["spawn_signal"] is False
    assert ev["clauses"]["fingerprint_changed"] is False
    assert ev["spawn"] is False


def test_frozen_fingerprint_unlatched_source_spawns() -> None:
    """AC4(iv) — a:35207 regression: unlatched attention must not freeze the chain."""
    digest = _digest(attention=[_LIVE_LANE])
    fp = spawn_fingerprint(digest["root"], digest["lanes"])
    state = {"last_spawn_fingerprint": fp, "last_spawn_at": 0.0}
    ev = evaluate_spawn_predicate(digest, state, lock={"holder": None}, now=time.time())
    assert ev["clauses"]["spawn_signal"] is True
    assert ev["clauses"]["fingerprint_changed"] is True
    assert ev["spawn"] is True


def test_frozen_fingerprint_latched_live_lane_no_spawn() -> None:
    """Falsifier guard — permanently-unread lane latched at ``turns`` must stay quiet."""
    digest = _digest(attention=[_LIVE_LANE])
    fp = spawn_fingerprint(digest["root"], digest["lanes"])
    state = {
        "last_spawn_fingerprint": fp,
        "last_spawn_at": 0.0,
        "served_lanes": {"11570": 5},
    }
    ev = evaluate_spawn_predicate(digest, state, lock={"holder": None}, now=time.time())
    assert ev["clauses"]["spawn_signal"] is False
    assert ev["clauses"]["fingerprint_changed"] is False
    assert ev["spawn"] is False


def test_fire_spawn_row_class_hold_after_bind_without_class() -> None:
    """Missing ROW_CLASS after bind hop ⇒ hold, not grok house generate."""
    posted: list[dict] = []
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["successor_model"] = "cursor/grok-4.6"
    digest["frictions"] = [
        {
            "id": "a:35997",
            "owner": "service:git_integration_worker",
            "category": "regression",
            "note": "GIW probe",
            "state": "open",
            "forcing": True,
        }
    ]
    digest["lanes"] = []
    state = {"friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"}}
    result = fire_spawn(
        "11960",
        digest["policy"],
        state,
        digest=digest,
        leftover={"leftover": "sit", "reason": "sit_no_todo", "todo": None},
        submit=lambda body: posted.append(body) or ({}, 200),
    )
    assert result["refused"] == "row_class_hold"
    assert posted == []


def test_record_spawn_service_latches_live_lane_turns() -> None:
    """AC2 — ``served_lanes`` mirrors ``served_closeouts`` for live attention."""
    from bus_watch.spawn_pending import actionable_attention, record_spawn_service

    state: dict = {"pending_spawn": {"thread_id": "10601"}}
    assert actionable_attention([_LIVE_LANE], state=state) == [_LIVE_LANE]
    record_spawn_service(state, [_LIVE_LANE])
    assert state["served_lanes"] == {"11570": 5}
    assert actionable_attention([_LIVE_LANE], state=state) == []
    assert state["successor_threads"] == ["10601"]


def test_tick_spawn_writes_fired_latch_on_low_success(monkeypatch) -> None:  # noqa: ANN001
    """S5 — mark_row_class_fired write path: successful LOW fire latches fid."""
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: {"holder": None}
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )
    monkeypatch.setattr("bus_watch.spawn_wake.fire.page_liaison", lambda *a: None)
    monkeypatch.setattr("bus_watch.spawn_wake.row_bind.page_liaison", lambda *a: None)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = "manage recycle git_integration_worker landed"
    digest["frictions"] = [
        {
            "id": "a:35997",
            "owner": "service:git_integration_worker",
            "category": "regression",
            "note": "probe",
            "state": "open",
            "forcing": True,
        }
    ]
    digest["lanes"] = []
    state = {
        "friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"},
        "row_class": {
            "a:35997": {"class": "low", "why": "one file", "row_id": "a:35997"}
        },
    }
    posted: list[dict] = []
    out = tick_spawn_on_wake(
        digest,
        state,
        "11960",
        submit=lambda body: (
            posted.append(body)
            or ({"execution_id": "e-low", "thread_id": "12099"}, 200)
        ),
    )
    assert out["action"] == "spawned"
    assert "a:35997" in (state.get("row_class_fired") or {})
    assert posted[0]["contract"] == "implement"
    assert posted[0]["_friction_id"] == "a:35997"


def test_tick_spawn_does_not_latch_fired_on_http_error(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.read_lock", lambda *_a, **_k: {"holder": None}
    )
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.maybe_forfeit_expired_lease", lambda *_a, **_k: False
    )
    monkeypatch.setattr("bus_watch.spawn_wake.fire.page_liaison", lambda *a: None)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["policy"]["now_row"] = "manage recycle git_integration_worker landed"
    digest["frictions"] = [
        {
            "id": "a:35997",
            "owner": "service:git_integration_worker",
            "category": "regression",
            "note": "probe",
            "state": "open",
            "forcing": True,
        }
    ]
    digest["lanes"] = []
    state = {
        "friction_rows_seen": {"a:35997": "2026-09-21T06:00:00Z"},
        "row_class": {
            "a:35997": {"class": "low", "why": "one file", "row_id": "a:35997"}
        },
    }
    out = tick_spawn_on_wake(
        digest,
        state,
        "11960",
        submit=lambda _body: ({"error": {"code": "boom", "message": "fail"}}, 500),
    )
    assert "a:35997" not in (state.get("row_class_fired") or {})
    assert out["fire"]["status_code"] == 500
