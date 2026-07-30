"""Dry-run materialization review surface for matter-scope action predicates.

Read-only companion to the read-time terminal-facts detector (Fable bind
``dd1858ae`` §3–§5): proposes stored ``predicate_form`` / ``valid_from`` values
for active matter-scope assertions and sorts every proposal into the auto or
review tier. Assertions outside the three operator-write-authorized matter
scopes are never proposed — they stay read-time derived.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from predicate_form.action_detection import distinct_dates_in_text
from predicate_form.action_enrichment import (
    DERIVATION_SOURCE,
    enrich_action_predicate_from_claim,
)
from predicate_form.action_vocabulary import TERMINAL_FUNCTORS, parse_action_predicate

from .assertion_quality import (
    entity_in_matter_valid_from_scope,
    matter_valid_from_scopes,
)
from .db import query

MATERIALIZE_REVIEW_VERSION = "materialize_review_v0"

# Bind §4 sizes auto-commit batches at 50–100 rows; below the floor the auto
# tier is not worth its own risk surface, so the sweep collapses to all-review.
AUTO_TIER_MIN_BATCH = 50

APPEAL_SCOPE = "case:boe19p-flintridge-appeal-2026"

TIER_AUTO = "auto"
TIER_REVIEW = "review"

_SCAN_COLS = (
    "id, entity_id, claim, review_status, confidence, valid_from, predicate_form"
)


@dataclass(frozen=True)
class MaterializationCandidate:
    """One proposed stored predicate for a single active matter-scope assertion."""

    assertion_id: int
    entity_id: str
    proposed_predicate_form: str
    stored_predicate_form: str | None
    functor: str
    action: str
    party: str
    proposed_valid_from: str | None
    stored_valid_from: str | None
    literal_span: str | None
    tier: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MaterializationReview:
    """Whole-sweep dry-run result: candidates plus tier accounting."""

    candidates: tuple[MaterializationCandidate, ...]
    scanned_count: int
    already_materialized_count: int
    auto_count: int
    review_count: int
    collapse_auto_to_review: bool
    detector_version: str
    version: str

    def effective_tier(self, candidate: MaterializationCandidate) -> str:
        """Tier after the small-batch collapse — the binding tier for any writer."""
        if self.collapse_auto_to_review:
            return TIER_REVIEW
        return candidate.tier

    def summary(self) -> dict[str, object]:
        return {
            "version": self.version,
            "detector_version": self.detector_version,
            "scanned_count": self.scanned_count,
            "already_materialized_count": self.already_materialized_count,
            "auto_count": self.auto_count,
            "review_count": self.review_count,
            "collapse_auto_to_review": self.collapse_auto_to_review,
            "candidate_count": len(self.candidates),
        }


@dataclass(frozen=True)
class _RowOutcome:
    candidate: MaterializationCandidate | None
    already_materialized: bool


def _scan_query() -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for scope in matter_valid_from_scopes():
        clauses.append("(entity_id = ? OR entity_id LIKE ?)")
        params.extend([scope, f"{scope}/%"])
    where = " OR ".join(clauses)
    sql = (
        f"SELECT {_SCAN_COLS} FROM assertions "
        f"WHERE superseded_by IS NULL AND ({where}) ORDER BY id"
    )
    return sql, tuple(params)


def scan_matter_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Active assertions inside the write-authorized matter scopes."""
    sql, params = _scan_query()
    rows = query(conn, sql, params)
    return [
        row for row in rows if entity_in_matter_valid_from_scope(str(row["entity_id"]))
    ]


def _stored_terminal_index(
    rows: list[dict[str, object]],
) -> dict[tuple[str, str], tuple[int, str]]:
    index: dict[tuple[str, str], tuple[int, str]] = {}
    for row in rows:
        stored = row.get("predicate_form")
        if not stored:
            continue
        pred = parse_action_predicate(str(stored), assertion_id=int(row["id"]))
        if pred is None or pred.functor not in TERMINAL_FUNCTORS:
            continue
        index.setdefault(pred.collision_key, (int(row["id"]), str(stored)))
    return index


def _epistemic_state(row: dict[str, object]) -> str | None:
    for key in ("review_status", "confidence"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _conflict_reason(
    row: dict[str, object],
    *,
    collision_key: tuple[str, str],
    proposed_predicate_form: str,
    terminal_index: dict[tuple[str, str], tuple[int, str]],
) -> str | None:
    conflict = terminal_index.get(collision_key)
    if conflict is None:
        return None
    blocking_id, blocking_form = conflict
    if blocking_id == int(row["id"]) or blocking_form == proposed_predicate_form:
        return None
    return f"conflicts_with_stored_terminal_{blocking_id}"


def _review_reasons(
    row: dict[str, object],
    *,
    functor: str,
    literal_span: str | None,
    dates: list[str],
    stored_predicate_form: str | None,
    conflict: str | None,
) -> list[str]:
    reasons: list[str] = []
    if functor in TERMINAL_FUNCTORS:
        reasons.append("disposition_requires_review")
    if str(row["entity_id"]).startswith(APPEAL_SCOPE):
        reasons.append("appeal_scope_requires_review")
    if not literal_span:
        reasons.append("no_literal_span")
    if not dates:
        reasons.append("no_literal_date")
    elif len(dates) > 1:
        reasons.append("ambiguous_literal_dates")
    if stored_predicate_form:
        reasons.append("stored_predicate_form_differs")
    if conflict is not None:
        reasons.append(conflict)
    return reasons


def _evaluate_row(
    row: dict[str, object],
    terminal_index: dict[tuple[str, str], tuple[int, str]],
) -> _RowOutcome:
    claim = str(row.get("claim") or "")
    preview = enrich_action_predicate_from_claim(
        claim,
        str(row["entity_id"]),
        assertion_id=int(row["id"]),
        valid_from=str(row["valid_from"]) if row.get("valid_from") else None,
        epistemic_state=_epistemic_state(row),
    )
    if preview is None:
        return _RowOutcome(candidate=None, already_materialized=False)

    stored_raw = row.get("predicate_form")
    stored = str(stored_raw) if stored_raw else None
    if stored == preview.predicate_form:
        return _RowOutcome(candidate=None, already_materialized=True)

    dates = distinct_dates_in_text(claim)
    conflict = _conflict_reason(
        row,
        collision_key=(preview.action, preview.party),
        proposed_predicate_form=preview.predicate_form,
        terminal_index=terminal_index,
    )
    reasons = _review_reasons(
        row,
        functor=preview.functor,
        literal_span=preview.matched_segment,
        dates=dates,
        stored_predicate_form=stored,
        conflict=conflict,
    )
    candidate = MaterializationCandidate(
        assertion_id=int(row["id"]),
        entity_id=str(row["entity_id"]),
        proposed_predicate_form=preview.predicate_form,
        stored_predicate_form=stored,
        functor=preview.functor,
        action=preview.action,
        party=preview.party,
        proposed_valid_from=dates[0] if len(dates) == 1 else None,
        stored_valid_from=str(row["valid_from"]) if row.get("valid_from") else None,
        literal_span=preview.claim_excerpt,
        tier=TIER_REVIEW if reasons else TIER_AUTO,
        reasons=tuple(reasons),
    )
    return _RowOutcome(candidate=candidate, already_materialized=False)


def review_materialization(conn: sqlite3.Connection) -> MaterializationReview:
    """Dry-run the matter-scope materialization sweep — proposes, never writes."""
    rows = scan_matter_rows(conn)
    terminal_index = _stored_terminal_index(rows)

    candidates: list[MaterializationCandidate] = []
    already_materialized = 0
    for row in rows:
        outcome = _evaluate_row(row, terminal_index)
        if outcome.already_materialized:
            already_materialized += 1
        if outcome.candidate is not None:
            candidates.append(outcome.candidate)

    auto_count = sum(1 for c in candidates if c.tier == TIER_AUTO)
    return MaterializationReview(
        candidates=tuple(candidates),
        scanned_count=len(rows),
        already_materialized_count=already_materialized,
        auto_count=auto_count,
        review_count=len(candidates) - auto_count,
        collapse_auto_to_review=auto_count < AUTO_TIER_MIN_BATCH,
        detector_version=DERIVATION_SOURCE,
        version=MATERIALIZE_REVIEW_VERSION,
    )
