"""Mechanical collision engine v0 — salience pre-speak guard (slice 1).

``detect_contradictions`` matches proposed action functors against stored
terminal rows on ``(action, party)`` key equality — no embedding, no FTS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .action_vocabulary import (
    TERMINAL_FUNCTORS,
    ActionPredicate,
    parse_action_predicate,
)

CollisionKind = Literal["contradiction", "superseded_by_candidate"]


@dataclass(frozen=True)
class Contradiction:
    kind: Literal["contradiction"]
    proposed: ActionPredicate
    blocking_assertion_id: int
    blocking_predicate_form: str
    reason: str


@dataclass(frozen=True)
class SupersededByCandidate:
    kind: Literal["superseded_by_candidate"]
    proposed: ActionPredicate
    terminal_assertion_id: int
    terminal_predicate_form: str
    reason: str


CollisionResult = Contradiction | SupersededByCandidate


def _stored_action_rows(
    stored: list[ActionPredicate | str],
) -> list[ActionPredicate]:
    rows: list[ActionPredicate] = []
    for item in stored:
        if isinstance(item, ActionPredicate):
            rows.append(item)
            continue
        parsed = parse_action_predicate(item)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _proposed_action_rows(
    proposed: list[ActionPredicate | str],
) -> list[ActionPredicate]:
    rows: list[ActionPredicate] = []
    for item in proposed:
        if isinstance(item, ActionPredicate):
            rows.append(item)
            continue
        parsed = parse_action_predicate(item)
        if parsed is not None:
            rows.append(parsed)
    return rows


def detect_contradictions(
    proposed: list[ActionPredicate | str],
    stored: list[ActionPredicate | str],
) -> list[CollisionResult]:
    """Return contradictions and superseded-by candidates for proposed vs stored rows.

    Rules (bind §S3):
    - ``denied(A,P,D) ⊗ request(A,P)`` → contradiction (same A, P)
    - ``pending(A,P,W)`` when terminal ``denied(A,P,*)`` exists → superseded-by candidate
    - Different actions on same party → no collision (negative control)
    """
    proposed_rows = _proposed_action_rows(proposed)
    stored_rows = _stored_action_rows(stored)
    terminal_by_key: dict[tuple[str, str], ActionPredicate] = {}
    for row in stored_rows:
        if row.functor not in TERMINAL_FUNCTORS:
            continue
        key = row.collision_key
        terminal_by_key.setdefault(key, row)

    results: list[CollisionResult] = []
    for prop in proposed_rows:
        key = prop.collision_key
        terminal = terminal_by_key.get(key)
        if terminal is None:
            continue
        if prop.functor == "request" and terminal.functor == "denied":
            aid = terminal.assertion_id
            if aid is None:
                continue
            results.append(
                Contradiction(
                    kind="contradiction",
                    proposed=prop,
                    blocking_assertion_id=aid,
                    blocking_predicate_form=terminal.to_predicate_form(),
                    reason=(
                        f"request({prop.action}, {prop.party}) contradicts "
                        f"stored denial at assertion {aid}"
                    ),
                )
            )
        elif prop.functor == "pending" and terminal.functor == "denied":
            aid = terminal.assertion_id
            if aid is None:
                continue
            results.append(
                SupersededByCandidate(
                    kind="superseded_by_candidate",
                    proposed=prop,
                    terminal_assertion_id=aid,
                    terminal_predicate_form=terminal.to_predicate_form(),
                    reason=(
                        f"pending({prop.action}, {prop.party}, {prop.wo_id}) "
                        f"superseded by terminal denial at assertion {aid}"
                    ),
                )
            )
    return results
