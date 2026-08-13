"""Tests for L2 orientation generator (7119 L2)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.l2_orientation import (
    L2_CONSTITUTION,
    compose_handoff_prompt,
    extract_lane_tip,
    find_latest_admit_turn,
    generate_l2_orientation,
    read_cse_state,
    render_arrival_card,
)


def _turn(*, n: int, body: str, subject: str = "test", from_agent: str = "seat-a") -> dict:
    return {
        "id": 1000 + n,
        "turn_number": n,
        "from": from_agent,
        "subject": subject,
        "body": body,
        "created_at": "2026-08-12T16:00:00Z",
    }


def test_constitution_is_live_snapshot() -> None:
    assert L2_CONSTITUTION == "live-snapshot"


def test_extract_lane_tip_picks_latest() -> None:
    turns = [
        _turn(n=1, body="old"),
        _turn(n=5, body="TYPE: PARKED\narc: 7119"),
    ]
    tip = extract_lane_tip(thread_id="6655", turns=turns)
    assert tip.turn_number == 5
    assert "PARKED" in (tip.body_excerpt or "")


def test_find_latest_admit_turn() -> None:
    turns = [
        _turn(n=1, body="x", from_agent="cursor-auto", subject="status:admitted job-1"),
        _turn(n=9, body="admit body", from_agent="cursor-auto", subject="status:admitted job-2"),
        _turn(n=10, body="other", from_agent="human"),
    ]
    bind = find_latest_admit_turn(turns)
    assert bind is not None
    assert bind.turn_number == 9
    assert "admit body" in bind.body_excerpt


def test_handoff_prompt_composes_three_slices() -> None:
    cse = read_cse_state(thread_id="nonexistent-thread-xyz")
    tip = extract_lane_tip(thread_id="6655", turns=[_turn(n=1, body="hello")])
    handoff = compose_handoff_prompt(
        cse=cse,
        tip=tip,
        obligations=[],
        admit_bind=None,
        generated_at="2026-08-12T16:00:00Z",
    )
    assert "cse_state:" in handoff
    assert "lane_tip:" in handoff
    assert "open_obligations:" in handoff
    assert "inheritance loop NOT closed" in handoff
    assert "recent_commits:" in handoff
    assert 'query: fs(op="recent_commits"' in handoff


def test_handoff_drops_recent_commits_body_when_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.cursor_auto import l2_orientation as mod

    monkeypatch.setattr(mod, "MAX_ARRIVAL_LINES", 8)
    monkeypatch.setattr(
        mod,
        "load_recent_commits_for_hop",
        lambda: {
            "head": "abc1234deadbeef",
            "commits": [
                {
                    "sha": "abc1234deadbeef",
                    "subject": "land foo",
                    "author": "t",
                    "authored_at": "2026-01-01T00:00:00Z",
                }
            ],
            "since": "last 8",
            "truncated": False,
        },
    )
    cse = read_cse_state(thread_id="nonexistent-thread-xyz")
    tip = extract_lane_tip(thread_id="6655", turns=[_turn(n=1, body="hello")])
    handoff = compose_handoff_prompt(
        cse=cse,
        tip=tip,
        obligations=[],
        admit_bind=None,
        generated_at="2026-08-12T16:00:00Z",
    )
    assert "body dropped for screen budget" in handoff
    assert "land foo" not in handoff
    assert 'query: fs(op="recent_commits"' in handoff
    assert 'since="abc1234deadbeef"' in handoff


def test_arrival_card_respects_line_budget() -> None:
    cse = read_cse_state(thread_id="6655")
    tip = extract_lane_tip(thread_id="6655", turns=[_turn(n=1, body="TYPE: PARKED")])
    card, dropped = render_arrival_card(
        thread_id="6655",
        generated_at="2026-08-12T16:00:00Z",
        cse=cse,
        tip=tip,
        obligations=[{"kind": "wake_owed", "status": "open"}],
        admit_bind=None,
    )
    assert "GENERATED ARRIVAL CARD" in card
    assert len(card.splitlines()) <= 45
    assert "constitution: live-snapshot" in card


def test_generate_with_admit_closes_inheritance_loop() -> None:
    turns = [
        _turn(n=8, body="admit", from_agent="cursor-auto", subject="status:admitted x"),
        _turn(n=9, body="TYPE: PARKED\nwake: chat", subject="parked"),
    ]
    result = generate_l2_orientation(
        thread_id="6655",
        turns=turns,
        generated_at="2026-08-12T16:00:00Z",
    )
    assert result.inheritance_loop_closed is True
    assert "admit_turn_bind" in result.handoff_prompt
    assert any(s.slice_name == "arc_open_items" and not s.queryable for s in result.sources)
