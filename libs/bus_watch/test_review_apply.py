"""Review-apply under — suggestions never park for the operator."""

from __future__ import annotations

from bus_watch.fable_lock import current_night_id
from bus_watch.spawn_wake.fire import body_for_leftover, fire_spawn, tick_spawn_on_wake
from bus_watch.spawn_wake.packet import _wire_submit_body
from bus_watch.spawn_wake.predicate import evaluate_spawn_predicate
from bus_watch.spawn_wake.review_apply import (
    build_review_apply_body,
    is_review_owing_apply,
    owing_review_apply,
    ready_review_apply_attention,
    review_apply_fired,
    review_apply_key,
)


def _review_turn(*, turn: int = 75) -> dict:
    return {
        "turn_number": turn,
        "thread": "11960",
        "subject": (
            "G6 PRE-LAND REVIEW 3967ecf (todo:liaison-row-class) — "
            "SUGGESTIONS, land_disposition CLEAR"
        ),
        "body": "verdict: SUGGESTIONS · land_disposition: clear",
    }


def _digest_with_review(turn: dict | None = None) -> dict:
    row = turn or _review_turn()
    return {
        "root": {
            "id": "11960",
            "recent_turns": [row],
            "unread_turns": [row],
        },
        "attention": [],
        "lanes": [],
        "frictions": [],
        "policy": {
            "ready": False,
            "max_hops_per_night": 8,
            "max_dispatches_per_night": 0,
            "spawn_grace_seconds": 0,
            "max_hop_minutes": 60,
            "wake_on_attention_only": True,
            "gear": "3-wake-on-attention",
            "successor_model": "cursor/grok-4.6",
            "successor_model_source": "override",
        },
        "register": "autonomous",
    }


def test_detects_suggestions_subject() -> None:
    assert is_review_owing_apply(_review_turn()) is True
    assert is_review_owing_apply({"subject": "DIGEST 11960", "body": ""}) is False


def test_owing_skips_already_fired() -> None:
    digest = _digest_with_review()
    state: dict = {}
    owed = owing_review_apply(digest, state)
    assert owed is not None
    assert owed["turn_number"] == 75
    state["review_apply_fired"] = {owed["key"]: "2026-09-21T06:55:00Z"}
    assert owing_review_apply(digest, state) is None


def test_attention_promotes_owing_review() -> None:
    digest = _digest_with_review()
    items = ready_review_apply_attention(digest, {})
    assert items[0]["kind"] == "review_apply"
    assert items[0]["id"] == "review-apply:11960#75"


def test_apply_body_is_lane_b_house_generate() -> None:
    review = owing_review_apply(_digest_with_review(), {})
    assert review is not None
    body = build_review_apply_body(
        "11960",
        {"max_hop_minutes": 60, "successor_model": "cursor/grok-4.6"},
        review,
    )
    assert body["lane"] == "B"
    assert body["contract"] == "none"
    assert body["model"] == "cursor/grok-4.6"
    assert body["_review_apply"] is True
    assert "prompt" not in body
    assert "source_ref" not in body
    assert "ALL suggestions" in body["message"]
    night = current_night_id()
    assert body["work_key"] == f"agent-bus:11960:review-apply:75:night-{night}"
    wired = _wire_submit_body(body)
    assert "_review_apply" not in wired
    assert wired["lane"] == "B"
    assert wired["prompt"]
    assert "prompt" in wired
    assert "ALL suggestions" in wired["prompt"]


def test_body_for_leftover_outranks_sit() -> None:
    digest = _digest_with_review()
    body = body_for_leftover(
        "11960",
        digest["policy"],
        {"leftover": "sit", "reason": "sit_no_todo", "todo": None},
        digest=digest,
        state={},
    )
    assert body is not None
    assert body.get("_review_apply") is True


def test_predicate_fires_when_ready_false_and_ide_held() -> None:
    digest = _digest_with_review()
    ev = evaluate_spawn_predicate(
        digest,
        {},
        lock={"holder": "ide:4febeb87-abd1-4f73-ab21-798eb2433801"},
    )
    assert ev["spawn"] is True
    assert ev["clauses"]["policy_ready"] is True
    assert ev["clauses"]["seat_lock_free"] is True
    assert "review_apply" in ev["spawn_signal_sources"]


def test_fire_spawn_does_not_page_review_apply(monkeypatch) -> None:
    pages: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.page_liaison",
        lambda root, subject, body: pages.append((root, subject, body)),
    )
    posted: list[dict] = []
    digest = _digest_with_review()
    state: dict = {}
    result = fire_spawn(
        "11960",
        digest["policy"],
        state,
        digest=digest,
        leftover={"leftover": "sit", "reason": "sit_no_todo", "todo": None},
        submit=lambda body: posted.append(body) or ({"execution_id": "e1"}, 200),
    )
    assert result["status_code"] == 200
    assert posted and posted[0]["contract"] == "none"
    assert "prompt" in posted[0]
    assert "source_ref" not in posted[0]
    assert pages == []


def test_tick_latches_review_apply_key() -> None:
    digest = _digest_with_review()
    state: dict = {"last_spawn_at": 0.0}
    result = tick_spawn_on_wake(
        digest,
        state,
        "11960",
        submit=lambda body: ({"execution_id": "e1", "thread_id": "12010"}, 200),
        is_terminal=lambda _pending: True,
    )
    assert result["action"] == "spawned"
    key = review_apply_key("11960", 75)
    assert review_apply_fired(state, key) is True
