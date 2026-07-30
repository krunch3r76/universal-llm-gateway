"""Dry-run valid_from materialization review surface for matter-scope assertions.

Read-only. Proposes a stored ``valid_from`` anchor for active matter-scope
assertions that have none, derived from a single literal date in the claim, and
sorts each proposal into the auto or review tier.

Write target is ``valid_from`` only. Action predicates stay read-time derived:
no consumer reads a stored action predicate (``claims_burst`` selects
``predicate_form`` and never uses it), while three unrelated consumers do read
the column as an asserted *state* predicate — the card current-status slot
(``card.py``), supersede candidacy by functor equality (``belief_guard.py``),
and the normalization ledger (``routes/assertions/_update.py`` rewrites all four
ledger columns on any explicit ``predicate_form`` PATCH). Writing a derived
action predicate there would be lossy and buy nothing. Binder verdict (D):
cortex://notes/system/threads/6386-materialize-verdict.md.

There is no auto-commit batch tier: the write-authorized population is small
enough that every write wants a named human ratification, which is cheaper than
batching machinery. ``requires_named_ratification`` is therefore always true.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from predicate_form.action_detection import distinct_dates_in_text
from predicate_form.action_enrichment import (
    DERIVATION_SOURCE,
    enrich_action_predicate_from_claim,
)
from predicate_form.action_vocabulary import TERMINAL_FUNCTORS

from .assertion_quality import (
    entity_in_matter_valid_from_scope,
    matter_valid_from_scopes,
)
from .db import query

MATERIALIZE_REVIEW_VERSION = "materialize_review_v1"

APPEAL_SCOPE = "case:boe19p-flintridge-appeal-2026"

TIER_AUTO = "auto"
TIER_REVIEW = "review"

_SCAN_COLS = (
    "id, entity_id, claim, review_status, confidence, valid_from, predicate_form"
)


@dataclass(frozen=True)
class ValidFromCandidate:
    """One proposed ``valid_from`` anchor for an assertion that has none."""

    assertion_id: int
    entity_id: str
    proposed_valid_from: str | None
    literal_dates: tuple[str, ...]
    functor: str
    action: str
    party: str
    literal_span: str | None
    # Reviewer context only — never a write target.
    stored_predicate_form: str | None
    tier: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MaterializationReview:
    """Whole-sweep dry-run result: candidates plus tier accounting."""

    candidates: tuple[ValidFromCandidate, ...]
    scanned_count: int
    already_anchored_count: int
    auto_count: int
    review_count: int
    detector_version: str
    version: str

    @property
    def requires_named_ratification(self) -> bool:
        """Every write is individually ratified — there is no auto-commit batch tier."""
        return True

    def summary(self) -> dict[str, object]:
        return {
            "version": self.version,
            "detector_version": self.detector_version,
            "write_target": "valid_from",
            "scanned_count": self.scanned_count,
            "already_anchored_count": self.already_anchored_count,
            "auto_count": self.auto_count,
            "review_count": self.review_count,
            "candidate_count": len(self.candidates),
            "requires_named_ratification": self.requires_named_ratification,
        }


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


def _has_valid_from(row: dict[str, object]) -> bool:
    raw = row.get("valid_from")
    return bool(raw and str(raw).strip())


def _epistemic_state(row: dict[str, object]) -> str | None:
    for key in ("review_status", "confidence"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _review_reasons(
    row: dict[str, object],
    *,
    functor: str,
    literal_span: str | None,
    dates: list[str],
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
    return reasons


def _evaluate_row(row: dict[str, object]) -> ValidFromCandidate | None:
    """Propose an anchor for one row, or None when nothing is proposable."""
    claim = str(row.get("claim") or "")
    preview = enrich_action_predicate_from_claim(
        claim,
        str(row["entity_id"]),
        assertion_id=int(row["id"]),
        epistemic_state=_epistemic_state(row),
    )
    if preview is None:
        return None

    dates = distinct_dates_in_text(claim)
    reasons = _review_reasons(
        row,
        functor=preview.functor,
        literal_span=preview.matched_segment,
        dates=dates,
    )
    stored_predicate = row.get("predicate_form")
    return ValidFromCandidate(
        assertion_id=int(row["id"]),
        entity_id=str(row["entity_id"]),
        proposed_valid_from=dates[0] if len(dates) == 1 else None,
        literal_dates=tuple(dates),
        functor=preview.functor,
        action=preview.action,
        party=preview.party,
        literal_span=preview.claim_excerpt,
        stored_predicate_form=str(stored_predicate) if stored_predicate else None,
        tier=TIER_REVIEW if reasons else TIER_AUTO,
        reasons=tuple(reasons),
    )


def review_materialization(conn: sqlite3.Connection) -> MaterializationReview:
    """Dry-run the matter-scope valid_from sweep — proposes, never writes."""
    rows = scan_matter_rows(conn)

    candidates: list[ValidFromCandidate] = []
    already_anchored = 0
    for row in rows:
        # A row that already carries an anchor has nothing to fill; proposing one
        # would overwrite an existing temporal anchor, not materialize a missing one.
        if _has_valid_from(row):
            already_anchored += 1
            continue
        candidate = _evaluate_row(row)
        if candidate is not None:
            candidates.append(candidate)

    auto_count = sum(1 for c in candidates if c.tier == TIER_AUTO)
    return MaterializationReview(
        candidates=tuple(candidates),
        scanned_count=len(rows),
        already_anchored_count=already_anchored,
        auto_count=auto_count,
        review_count=len(candidates) - auto_count,
        detector_version=DERIVATION_SOURCE,
        version=MATERIALIZE_REVIEW_VERSION,
    )
