"""Collision engine v0 — salience slice 1 acceptance tests."""

from __future__ import annotations

from predicate_form.action_vocabulary import ActionPredicate
from predicate_form.collision import (
    Contradiction,
    SupersededByCandidate,
    detect_contradictions,
)


def _denied(
    action: str,
    party: str,
    date: str,
    *,
    assertion_id: int,
) -> ActionPredicate:
    return ActionPredicate(
        functor="denied",
        action=action,
        party=party,
        date=date,
        assertion_id=assertion_id,
    )


def _request(action: str, party: str) -> ActionPredicate:
    return ActionPredicate(
        functor="request",
        action=action,
        party=party,
    )


def _pending(action: str, party: str, wo_id: str) -> ActionPredicate:
    return ActionPredicate(
        functor="pending",
        action=action,
        party=party,
        wo_id=wo_id,
    )


def test_ac1_request_denied_same_action_party_contradiction() -> None:
    stored = [_denied("spread_extension", "chase", "2026-06-26", assertion_id=20701)]
    proposed = [_request("spread_extension", "chase")]
    results = detect_contradictions(proposed, stored)
    assert len(results) == 1
    hit = results[0]
    assert isinstance(hit, Contradiction)
    assert hit.blocking_assertion_id == 20701
    assert hit.blocking_predicate_form == "denied(spread_extension, chase, 2026-06-26)"


def test_ac2_pending_superseded_by_terminal_denied() -> None:
    stored = [_denied("spread_extension", "chase", "2026-06-26", assertion_id=20701)]
    proposed = [_pending("spread_extension", "chase", "953902037")]
    results = detect_contradictions(proposed, stored)
    assert len(results) == 1
    hit = results[0]
    assert isinstance(hit, SupersededByCandidate)
    assert hit.terminal_assertion_id == 20701


def test_ac4_negative_control_different_actions_no_contradiction() -> None:
    stored = [_denied("spread_extension", "chase", "2026-06-26", assertion_id=20701)]
    proposed = [_request("payment_reduction", "chase")]
    results = detect_contradictions(proposed, stored)
    assert results == []


def test_string_predicate_forms_accepted() -> None:
    stored = ["denied(spread_extension, chase, 2026-06-26)"]
    proposed = ["request(spread_extension, chase)"]
    # Without assertion_id on stored string rows, contradictions require ids — empty.
    assert detect_contradictions(proposed, stored) == []

    stored_with_id = [
        ActionPredicate(
            functor="denied",
            action="spread_extension",
            party="chase",
            date="2026-06-26",
            assertion_id=20701,
        )
    ]
    results = detect_contradictions(proposed, stored_with_id)
    assert len(results) == 1
    assert isinstance(results[0], Contradiction)
