"""Controlled action vocabulary v0 — salience collision substrate (slice 1).

Functors and action enum per bind cortex://notes/system/threads/
6386-salience-layer-architecture-bind.md §S3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .action_patterns import (
    ACTION_VOCAB_BY_DOMAIN,
    ACTION_VOCAB_V0,
    PARTY_BY_DOMAIN,
)
from .parser import Predicate, PredicateParseError, parse, unparse

__all__ = [
    "ACTION_VOCAB_BY_DOMAIN",
    "ACTION_VOCAB_V0",
    "ActionPredicate",
    "Functor",
    "PARTY_BY_DOMAIN",
    "PROCESS_FUNCTORS",
    "TERMINAL_FUNCTORS",
    "parse_action_predicate",
    "party_for_entity",
    "party_from_entity_id",
]

Functor = Literal["request", "denied", "granted", "pending"]

TERMINAL_FUNCTORS: frozenset[str] = frozenset({"denied", "granted"})
PROCESS_FUNCTORS: frozenset[str] = frozenset({"request", "pending"})


@dataclass(frozen=True)
class ActionPredicate:
    """Parsed action-typed predicate_form."""

    functor: Functor
    action: str
    party: str
    date: str | None = None
    wo_id: str | None = None
    assertion_id: int | None = None
    epistemic_state: str | None = None

    @property
    def collision_key(self) -> tuple[str, str]:
        return (self.action, self.party)

    @property
    def is_terminal(self) -> bool:
        return self.functor in TERMINAL_FUNCTORS

    def to_predicate_form(self) -> str:
        if self.functor == "request":
            return unparse(Predicate("request", (self.action, self.party)))
        if self.functor == "pending":
            wo = self.wo_id or "unknown"
            return unparse(Predicate("pending", (self.action, self.party, wo)))
        if self.date:
            return unparse(Predicate(self.functor, (self.action, self.party, self.date)))
        return unparse(Predicate(self.functor, (self.action, self.party)))


def parse_action_predicate(
    predicate_form: str,
    *,
    assertion_id: int | None = None,
    epistemic_state: str | None = None,
) -> ActionPredicate | None:
    """Parse a predicate_form string into ActionPredicate, or None if not action-typed."""
    try:
        p = parse(predicate_form)
    except PredicateParseError:
        return None
    if p.name not in TERMINAL_FUNCTORS | PROCESS_FUNCTORS:
        return None

    action: str | None = None
    party: str | None = None
    date: str | None = None
    wo_id: str | None = None
    functor: Functor | None = None

    if p.name == "request" and len(p.args) == 2:
        action, party = p.args
        functor = "request"
    elif p.name == "pending" and len(p.args) == 3:
        action, party, wo_id = p.args
        functor = "pending"
    elif p.name in TERMINAL_FUNCTORS and len(p.args) in (2, 3):
        action, party = p.args[0], p.args[1]
        date = p.args[2] if len(p.args) == 3 else None
        if date == "unknown":
            date = None
        functor = p.name  # type: ignore[assignment]
    else:
        return None

    if action not in ACTION_VOCAB_V0:
        return None

    return ActionPredicate(
        functor=functor,  # type: ignore[arg-type]
        action=action,
        party=party,
        date=date,
        wo_id=wo_id,
        assertion_id=assertion_id,
        epistemic_state=epistemic_state,
    )


def party_from_entity_id(entity_id: str) -> str | None:
    """Derive servicer party slug from a prefixed entity id (e.g. account:chase-mortgage-8787)."""
    if ":" not in entity_id:
        return None
    slug = entity_id.split(":", 1)[1]
    token = slug.split("-", 1)[0].strip().lower()
    return token or None


def party_for_entity(entity_id: str, *, domain: str | None = None) -> str | None:
    """Resolve party slug, preferring per-domain constants over slug-token derivation."""
    if domain is not None and domain in PARTY_BY_DOMAIN:
        return PARTY_BY_DOMAIN[domain]
    return party_from_entity_id(entity_id)
