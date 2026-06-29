"""Verdict table (design §3.2)."""

from __future__ import annotations

from typing import Any

from .fp_controls import apply_fp_controls
from .models import CandidateClause, Finding, FpCounters


def apply_verdict(
    *,
    path: str,
    candidate: CandidateClause,
    full_text: str,
    verdict_hint: str,
    row: dict[str, Any] | None,
    alignment_score: float | None,
    counters: FpCounters,
) -> Finding | None:
    if verdict_hint == "advisory":
        return Finding(
            verdict="ADVISORY",
            path=path,
            entity_id=candidate.entity_id,
            predicate_form=candidate.predicate_form,
            line_start=candidate.line_start,
            line_end=candidate.line_end,
            clause=candidate.clause,
            severity="warn",
        )

    skip, _ = apply_fp_controls(
        path=path,
        clause=candidate.clause,
        full_text=full_text,
        bind_score=candidate.bind_score,
        alignment_score=alignment_score,
        counters=counters,
    )
    if skip:
        if verdict_hint == "stale_candidate":
            return Finding(
                verdict="SKIPPED_FP",
                path=path,
                entity_id=candidate.entity_id,
                predicate_form=candidate.predicate_form,
                line_start=candidate.line_start,
                line_end=candidate.line_end,
                clause=candidate.clause,
                severity="info",
            )
        return None

    if verdict_hint == "stale_candidate" and row:
        return Finding(
            verdict="STALE",
            path=path,
            entity_id=candidate.entity_id,
            predicate_form=candidate.predicate_form,
            line_start=candidate.line_start,
            line_end=candidate.line_end,
            clause=candidate.clause,
            assertion_id=int(row["id"]),
            severity="flag",
        )

    if verdict_hint == "search_only_stale":
        return None

    if verdict_hint == "aligned":
        return Finding(
            verdict="ALIGNED",
            path=path,
            entity_id=candidate.entity_id,
            predicate_form=candidate.predicate_form,
            line_start=candidate.line_start,
            line_end=candidate.line_end,
            clause=candidate.clause,
            severity="info",
        )

    if verdict_hint == "uncited_volatile":
        return Finding(
            verdict="UNCITED_VOLATILE",
            path=path,
            entity_id=candidate.entity_id,
            predicate_form=candidate.predicate_form,
            line_start=candidate.line_start,
            line_end=candidate.line_end,
            clause=candidate.clause,
            severity="warn",
        )

    return None
