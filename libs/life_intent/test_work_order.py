"""Work order rendering and lane lookup tests."""

from __future__ import annotations

from life_intent.proposal_store import clear_store, create_proposal, get_proposal
from life_intent.registry import load_registry
from life_intent.work_order import lookup_lane, render_work_order


def test_lane_lookup_all_verbs() -> None:
    reg = load_registry()
    for verb in reg.verbs:
        lane = lookup_lane(verb, reg)
        assert lane == reg.verbs[verb].lane


def test_work_order_has_no_dispatch_vocabulary() -> None:
    forbidden = ("dispatch", "team_dispatch", "op=", "role=", "contract=", "cursor-sdk")
    intent = {
        "verb": "investigate",
        "subject": "reminder timing",
        "detail": "Notifications arrive twice.",
        "urgency": "normal",
    }
    text = render_work_order(intent).lower()
    for token in forbidden:
        assert token not in text


def test_work_order_fix_verb() -> None:
    intent = {
        "verb": "fix",
        "subject": "login timeout",
        "detail": "Users see timeout after 30 seconds.",
        "urgency": "soon",
    }
    text = render_work_order(intent)
    assert "work item" in text.lower()
    assert "time-sensitive" in text.lower()


def test_proposal_store_ttl_roundtrip() -> None:
    clear_store()
    pid = create_proposal(
        normalized_intent={"verb": "investigate", "subject": "x", "detail": "y" * 5},
        work_order="scout",
        verb="investigate",
        lane="recon",
    )
    row = get_proposal(pid)
    assert row is not None
    assert row.status == "open"
    clear_store()
