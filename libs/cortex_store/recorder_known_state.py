"""Recorder/life known-state gate — block same-anchor near-verbatim re-dumps."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Protocol

from .claim_hash import compute_claim_hash
from .db import query
from .models import AssertionCreate
from .near_dup import (
    DEDUP_FTS_CANDIDATE_LIMIT,
    DEDUP_SIMILARITY_THRESHOLD,
    _build_fts_query,
)

ANCHOR_RE = re.compile(r"\d{4}-\d{2}-\d{2}#[\w-]+(?:-[\w-]+)*")
_CORRECTION_MARKERS = re.compile(
    r"\b(CORRECTION|supersed|correct(?:ion|ed|ing)?|amend)\b", re.I
)

_LIFE_ENTITY_TYPES = frozenset(
    {
        "person",
        "organization",
        "matter",
        "document",
        "todo",
        "account",
        "property",
        "condition",
        "event",
        "journal",
    }
)
_LIFE_ENTITY_PREFIXES = frozenset(f"{t}:" for t in _LIFE_ENTITY_TYPES)


class AssertionBody(Protocol):
    entity_id: str
    claim: str
    evidence: str
    evidence_uris: list[str] | None
    attributes: dict[str, Any] | None
    force: bool
    supersedes_id: int | None


@dataclass(frozen=True)
class PriorRow:
    id: int
    claim: str
    review_status: str | None


@dataclass(frozen=True)
class KnownStateResult:
    already_known: bool
    known_state_reason: str | None = None
    matched_assertion_id: int | None = None
    anchor: str | None = None


def extract_anchors(texts: list[str | None]) -> set[str]:
    """Parse event_anchor tokens from claim/evidence/URI strings."""
    anchors: set[str] = set()
    for text in texts:
        if not text:
            continue
        anchors.update(ANCHOR_RE.findall(text))
    return anchors


def score_same_meaning(claim: str, prior_claim: str) -> float:
    """Lexical same-meaning score via SequenceMatcher (not semantic)."""
    return SequenceMatcher(None, claim.lower(), prior_claim.lower()).ratio()


def _is_correction_intent(texts: list[str | None]) -> bool:
    return any(_CORRECTION_MARKERS.search(t) for t in texts if t)


def should_apply_recorder_known_state(
    body: AssertionBody,
    *,
    entity_type: str | None = None,
) -> bool:
    """True for life/Recorder capture lane only (R A1)."""
    attrs = body.attributes or {}
    if attrs.get("domain") == "life":
        return True
    if attrs.get("recorder_capture") or attrs.get("capture_lane") == "recorder":
        return True
    entity_id = body.entity_id
    prefix = entity_id.split(":", 1)[0] + ":" if ":" in entity_id else ""
    if prefix in _LIFE_ENTITY_PREFIXES:
        return True
    if entity_type and entity_type in _LIFE_ENTITY_TYPES:
        return True
    return False


def lookup_priors_by_anchor(
    conn: sqlite3.Connection,
    entity_id: str,
    anchors: set[str],
) -> list[PriorRow]:
    """Live + staged rows on entity sharing an anchor token."""
    if not anchors:
        return []
    clauses = []
    params: list[object] = [entity_id]
    for anchor in sorted(anchors):
        like = f"%{anchor}%"
        clauses.append(
            "(claim LIKE ? OR evidence LIKE ? OR COALESCE(evidence_uris, '') LIKE ?)"
        )
        params.extend([like, like, like])
    sql = (
        "SELECT id, claim, review_status FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL AND ("
        + " OR ".join(clauses)
        + ") ORDER BY id DESC"
    )
    rows = query(conn, sql, tuple(params))
    return [
        PriorRow(
            id=int(r["id"]),
            claim=str(r["claim"]),
            review_status=r.get("review_status"),
        )
        for r in rows
    ]


def _best_lexical_prior(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    *,
    exclude_id: int | None = None,
) -> tuple[PriorRow | None, float]:
    fts_query = _build_fts_query(claim)
    if not fts_query:
        return None, 0.0
    try:
        rows = conn.execute(
            "SELECT a.id, a.claim, a.review_status "
            "FROM assertions a "
            "WHERE a.id IN ("
            "  SELECT rowid FROM assertions_fts WHERE assertions_fts MATCH ?"
            ") "
            "AND a.entity_id = ? "
            "AND a.superseded_by IS NULL "
            "AND (? IS NULL OR a.id != ?) "
            "LIMIT ?",
            (
                fts_query,
                entity_id,
                exclude_id,
                exclude_id,
                DEDUP_FTS_CANDIDATE_LIMIT,
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return None, 0.0
    best: PriorRow | None = None
    best_score = 0.0
    claim_lower = claim.lower()
    for row in rows:
        prior_claim = str(row[1])
        ratio = SequenceMatcher(None, claim_lower, prior_claim.lower()).ratio()
        if ratio > best_score:
            best_score = ratio
            best = PriorRow(
                id=int(row[0]),
                claim=prior_claim,
                review_status=row[2],
            )
    return best, best_score


def classify_correction_vs_redump(
    *,
    claim: str,
    evidence: str,
    prior: PriorRow,
    force: bool,
    supersedes_id: int | None,
    score: float,
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
) -> bool:
    """True when write should proceed (correction escape)."""
    if force and supersedes_id is not None:
        return True
    if _is_correction_intent([claim, evidence]) and score < threshold:
        return True
    return False


def check_recorder_known_state(
    conn: sqlite3.Connection,
    body: AssertionCreate,
    *,
    force: bool | None = None,
    supersedes_id: int | None = None,
) -> KnownStateResult:
    """Pre-write gate: block same-anchor high-lexical re-dumps."""
    use_force = body.force if force is None else force
    use_supersedes = body.supersedes_id if supersedes_id is None else supersedes_id

    if use_force and use_supersedes is not None:
        target = query(
            conn,
            "SELECT id FROM assertions WHERE id = ? AND superseded_by IS NULL",
            (use_supersedes,),
        )
        if target:
            return KnownStateResult(already_known=False)

    texts = [body.claim, body.evidence, *(body.evidence_uris or [])]
    anchors = extract_anchors(texts)
    primary_anchor = next(iter(sorted(anchors)), None)

    claim_hash = compute_claim_hash(body.entity_id, body.claim)
    exact = query(
        conn,
        "SELECT id FROM assertions WHERE entity_id = ? AND claim_hash = ? "
        "AND superseded_by IS NULL",
        (body.entity_id, claim_hash),
    )
    if exact:
        return KnownStateResult(
            already_known=True,
            known_state_reason="exact_claim_hash",
            matched_assertion_id=int(exact[0]["id"]),
            anchor=primary_anchor,
        )

    priors = lookup_priors_by_anchor(conn, body.entity_id, anchors)
    if priors:
        best = max(
            priors,
            key=lambda p: score_same_meaning(body.claim, p.claim),
        )
        score = score_same_meaning(body.claim, best.claim)
        if classify_correction_vs_redump(
            claim=body.claim,
            evidence=body.evidence,
            prior=best,
            force=use_force,
            supersedes_id=use_supersedes,
            score=score,
        ):
            return KnownStateResult(already_known=False)
        if score >= DEDUP_SIMILARITY_THRESHOLD:
            reason = "same_anchor_high_lexical"
            matched = best.id
            staged = [p for p in priors if p.review_status == "staged"]
            if staged:
                reason = "staged_sibling_collapse"
                matched = staged[0].id
            return KnownStateResult(
                already_known=True,
                known_state_reason=reason,
                matched_assertion_id=matched,
                anchor=primary_anchor,
            )

    fts_prior, fts_score = _best_lexical_prior(conn, body.entity_id, body.claim)
    if fts_prior and fts_score >= DEDUP_SIMILARITY_THRESHOLD and anchors:
        if not classify_correction_vs_redump(
            claim=body.claim,
            evidence=body.evidence,
            prior=fts_prior,
            force=use_force,
            supersedes_id=use_supersedes,
            score=fts_score,
        ):
            return KnownStateResult(
                already_known=True,
                known_state_reason="same_anchor_high_lexical",
                matched_assertion_id=fts_prior.id,
                anchor=primary_anchor,
            )

    return KnownStateResult(already_known=False)


def check_assert_op_known_state(
    conn: sqlite3.Connection,
    args: dict[str, Any],
) -> KnownStateResult:
    """Patch preflight for imprint assert ops."""
    body = AssertionCreate.model_validate(args)
    if not should_apply_recorder_known_state(body):
        return KnownStateResult(already_known=False)
    return check_recorder_known_state(conn, body)


def check_patch_assert_known_state(
    conn: sqlite3.Connection,
    op_plan: list[dict[str, Any]],
) -> KnownStateResult:
    """Return first already_known hit across assert ops in a patch plan."""
    for entry in op_plan:
        if str(entry.get("op") or "") != "assert":
            continue
        result = check_assert_op_known_state(conn, dict(entry.get("args") or {}))
        if result.already_known:
            return result
    return KnownStateResult(already_known=False)


__all__ = [
    "ANCHOR_RE",
    "KnownStateResult",
    "PriorRow",
    "check_assert_op_known_state",
    "check_patch_assert_known_state",
    "check_recorder_known_state",
    "classify_correction_vs_redump",
    "extract_anchors",
    "lookup_priors_by_anchor",
    "score_same_meaning",
    "should_apply_recorder_known_state",
]
