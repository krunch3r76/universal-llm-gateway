"""Clause extraction, fact classification, present-tense gate, entity binding."""

from __future__ import annotations

import re
from typing import Callable

from .constants import (
    BIND_ADMIT_THRESHOLD,
    ENTITY_ID_RE,
    INCOME_RE,
    PAST_TENSE_RE,
    PRESENT_HEAD_RE,
    ROLE_RE,
    TRANSPORT_RE,
    WORKFLOW_RE,
)
from .models import CandidateClause

CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n(?=[-*]\s)|\n(?=\*\*[^*]+:\*\*)")
ENTITY_PATTERN = re.compile(ENTITY_ID_RE)
PAST_PATTERN = re.compile(PAST_TENSE_RE, re.IGNORECASE)
PRESENT_HEAD_PATTERN = re.compile(PRESENT_HEAD_RE, re.IGNORECASE)
TRANSPORT_PATTERN = re.compile(TRANSPORT_RE, re.IGNORECASE)
INCOME_PATTERN = re.compile(INCOME_RE, re.IGNORECASE)
ROLE_PATTERN = re.compile(ROLE_RE, re.IGNORECASE)
WORKFLOW_PATTERN = re.compile(WORKFLOW_RE, re.IGNORECASE)


def split_clauses(text: str) -> list[tuple[str, int, int]]:
    lines = text.splitlines()
    joined = "\n".join(lines)
    parts = CLAUSE_SPLIT_RE.split(joined)
    out: list[tuple[str, int, int]] = []
    cursor = 0
    for part in parts:
        part = part.strip()
        if not part:
            cursor += 1
            continue
        idx = joined.find(part, cursor)
        if idx < 0:
            idx = cursor
        start_line = joined[:idx].count("\n") + 1
        end_line = start_line + part.count("\n")
        out.append((part, start_line, end_line))
        cursor = idx + len(part)
    return out


def passes_present_tense_gate(clause: str) -> bool:
    if PAST_PATTERN.search(clause) and not PRESENT_HEAD_PATTERN.search(clause):
        return False
    return True


def classify_fact(clause: str) -> tuple[str, str] | None:
    if TRANSPORT_PATTERN.search(clause):
        return ("transport", "status(...)")
    if INCOME_PATTERN.search(clause):
        return ("income", "status(...,engaged_in_*)")
    if ROLE_PATTERN.search(clause):
        return ("role", "role(...)")
    if WORKFLOW_PATTERN.search(clause):
        return ("workflow", "workflow_state")
    return None


def bind_entity(
    clause: str,
    *,
    principal: str | None,
    search_fn: Callable[[str], list[dict]] | None = None,
) -> tuple[str | None, float | None, bool]:
    match = ENTITY_PATTERN.search(clause)
    if match:
        return match.group(0), 1.0, False
    if ROLE_PATTERN.search(clause) and principal:
        return principal, 0.9, False
    if principal and ("handoff" in clause.lower() or "operational" in clause.lower()):
        return principal, 0.8, False
    if search_fn:
        hits = search_fn(clause)
        if hits:
            score = float(hits[0].get("score", 0))
            entity_id = hits[0].get("entity_id")
            if score >= BIND_ADMIT_THRESHOLD and entity_id:
                return str(entity_id), score, False
            if score < BIND_ADMIT_THRESHOLD:
                return entity_id, score, True
    return None, None, False


def extract_candidates(
    text: str,
    *,
    principal: str | None = None,
    search_fn: Callable[[str], list[dict]] | None = None,
) -> list[CandidateClause]:
    candidates: list[CandidateClause] = []
    for clause, line_start, line_end in split_clauses(text):
        if not passes_present_tense_gate(clause):
            continue
        classified = classify_fact(clause)
        if not classified:
            continue
        fact_class, predicate_form = classified
        entity_id, bind_score, advisory = bind_entity(
            clause, principal=principal, search_fn=search_fn
        )
        candidates.append(
            CandidateClause(
                entity_id=entity_id,
                fact_class=fact_class,
                predicate_form=predicate_form,
                clause=clause,
                line_start=line_start,
                line_end=line_end,
                bind_score=bind_score,
                advisory_only=advisory,
            )
        )
    return candidates
