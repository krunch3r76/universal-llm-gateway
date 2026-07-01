"""Fetch-first cross-reference engine."""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from .constants import ANTONYM_PAIRS
from .fp_controls import extract_status_tokens, parse_events_json
from .gate import filter_active_eligible
from .models import CandidateClause


def _predicate_matches(fact_class: str, predicate_form: str | None) -> bool:
    pf = (predicate_form or "").lower()
    if fact_class in ("transport", "income"):
        return "status(" in pf
    if fact_class == "role":
        return pf.startswith("role(")
    if fact_class == "workflow":
        return "workflow" in pf
    return False


def _antonym_contradiction(prose_tokens: set[str], claim_tokens: set[str]) -> bool:
    for a, b in ANTONYM_PAIRS:
        if (a in prose_tokens and b in claim_tokens) or (
            b in prose_tokens and a in claim_tokens
        ):
            return True
    return False


def _assertion_tokens(row: dict[str, Any]) -> set[str]:
    tokens = extract_status_tokens(str(row.get("claim", "")))
    for atom in parse_events_json(row.get("events_json")):
        tokens |= extract_status_tokens(atom)
    return tokens


def cross_reference_candidate(
    candidate: CandidateClause,
    *,
    fetch_fn: Callable[[str], list[dict[str, Any]]],
    search_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    analyze_impact_fn: Callable[[str, str], float] | None = None,
) -> tuple[str, dict[str, Any] | None, float | None]:
    """Return (verdict_hint, contradicting_row, alignment_score)."""
    if candidate.advisory_only or not candidate.entity_id:
        return "advisory", None, None

    rows = fetch_fn(candidate.entity_id)
    active = [
        row
        for row in filter_active_eligible(rows)
        if _predicate_matches(candidate.fact_class, row.get("predicate_form"))
    ]
    prose_tokens = extract_status_tokens(candidate.clause)

    for row in active:
        claim_tokens = _assertion_tokens(row)
        if _antonym_contradiction(prose_tokens, claim_tokens):
            alignment = None
            if analyze_impact_fn:
                alignment = analyze_impact_fn(candidate.entity_id, candidate.clause)
            return "stale_candidate", row, alignment

    if search_fn:
        hits = search_fn(candidate.clause)
        for hit in hits:
            entity_id = hit.get("entity_id")
            if not entity_id:
                continue
            search_rows = fetch_fn(str(entity_id))
            search_active = filter_active_eligible(search_rows)
            for row in search_active:
                if _predicate_matches(candidate.fact_class, row.get("predicate_form")):
                    if _antonym_contradiction(
                        prose_tokens, _assertion_tokens(row)
                    ):
                        return "search_only_stale", row, hit.get("score")
                    return "aligned", row, hit.get("score")

    if active:
        return "aligned", active[0], 0.9
    if candidate.fact_class in ("transport", "income"):
        return "uncited_volatile", None, None
    return "no_match", None, None
