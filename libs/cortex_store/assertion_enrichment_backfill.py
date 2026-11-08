"""Resumable assertion-enrichment backfill (events + prospective).

Selects non-superseded assertions, skips junk claims, dispatches the
``assertion-enrichment`` pipeline via Stargate with optional ``redo`` overwrite.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

Kind = Literal["events", "prospective"]

_PIPELINE_ID = "assertion-enrichment"

_QUESTION_PREFIX = re.compile(r"^\s*(what|when|why|how|which)\b", re.I)


class SkipClass(StrEnum):
    ASSISTANT_PREFIX = "assistant_prefix"
    UNGROUNDED = "ungrounded"
    EMPTY_CLAIM = "empty_claim"


def enrichment_skip_reason(claim: str | None) -> SkipClass | None:
    text = (claim or "").strip()
    if not text:
        return SkipClass.EMPTY_CLAIM
    if text.startswith("Assistant:"):
        return SkipClass.ASSISTANT_PREFIX
    if "[UNGROUNDED]" in text:
        return SkipClass.UNGROUNDED
    return None


def prospective_starts_with_what(text: str | None) -> bool:
    if not text:
        return False
    first = text.strip().split(None, 1)[0] if text.strip() else ""
    return first.lower() == "what"


def prospective_question_form_share(summaries: list[str | None]) -> float:
    nonempty = [s for s in summaries if s and s.strip()]
    if not nonempty:
        return 0.0
    n_what = sum(1 for s in nonempty if prospective_starts_with_what(s))
    return n_what / len(nonempty)


def _field_gap_sql(kind: Kind) -> str:
    col = "events_json" if kind == "events" else "prospective_summary"
    return f"({col} IS NULL OR TRIM({col}) = '')"


def _kinds_clause(kinds: tuple[Kind, ...], *, redo: bool) -> str:
    if redo:
        return "1=1"
    parts = [_field_gap_sql(k) for k in kinds]
    return f"({' OR '.join(parts)})"


@dataclass
class BackfillCountReport:
    active_total: int = 0
    eligible: int = 0
    skip_by_class: dict[str, int] = field(default_factory=dict)


def count_backfill_candidates(
    conn: sqlite3.Connection,
    *,
    kinds: tuple[Kind, ...],
    redo: bool,
) -> BackfillCountReport:
    report = BackfillCountReport()
    rows = conn.execute(
        "SELECT id, claim FROM assertions WHERE superseded_by IS NULL ORDER BY id"
    ).fetchall()
    report.active_total = len(rows)
    kinds_sql = _kinds_clause(kinds, redo=redo)
    for row in rows:
        reason = enrichment_skip_reason(row["claim"])
        if reason is not None:
            report.skip_by_class[reason.value] = (
                report.skip_by_class.get(reason.value, 0) + 1
            )
            continue
        if not redo:
            detail = conn.execute(
                f"SELECT 1 FROM assertions WHERE id = ? AND superseded_by IS NULL "
                f"AND {kinds_sql}",
                (row["id"],),
            ).fetchone()
            if not detail:
                continue
        report.eligible += 1
    return report


def fetch_candidate_batch(
    conn: sqlite3.Connection,
    *,
    kinds: tuple[Kind, ...],
    redo: bool,
    after_id: int,
    limit: int,
    exclude_ids: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    kinds_sql = _kinds_clause(kinds, redo=redo)
    params: list[Any] = []
    clauses = ["superseded_by IS NULL", "id > ?", kinds_sql]
    params.append(after_id)
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        clauses.append(f"id NOT IN ({placeholders})")
        params.extend(sorted(exclude_ids))

    sql = (
        "SELECT id, claim, entity_id, confidence, prospective_summary, events_json "
        f"FROM assertions WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?"
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if enrichment_skip_reason(row["claim"]) is not None:
            continue
        out.append(dict(row))
    return out


def events_array_len(events_json: str | None) -> int:
    if not events_json or not str(events_json).strip():
        return 0
    try:
        parsed = json.loads(events_json)
    except json.JSONDecodeError:
        return -1
    if not isinstance(parsed, list):
        return -1
    return len(parsed)


def field_populated(conn: sqlite3.Connection, assertion_id: int, kind: Kind) -> bool:
    col = "events_json" if kind == "events" else "prospective_summary"
    row = conn.execute(
        f"SELECT {col} AS v FROM assertions WHERE id = ?", (assertion_id,)
    ).fetchone()
    if not row:
        return False
    val = row["v"]
    return val is not None and str(val).strip() != ""


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "cursor_id": 0,
            "outcomes": {},
            "stats": {"ok": 0, "failed": 0, "skipped": 0},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def pipeline_payload(
    row: dict[str, Any],
    *,
    kinds: list[str],
    redo: bool,
) -> dict[str, Any]:
    return {
        "model": _PIPELINE_ID,
        "messages": [{"role": "user", "content": "enrich"}],
        "pipeline_options": {
            "assertion_id": row["id"],
            "claim": row["claim"],
            "entity_id": row.get("entity_id"),
            "confidence": row.get("confidence") or "believed",
            "kinds": kinds,
            "redo": redo,
        },
    }


def record_outcome(
    state: dict[str, Any],
    assertion_id: int,
    outcome: str,
    *,
    attempts: int | None = None,
) -> None:
    outcomes = state.setdefault("outcomes", {})
    entry: dict[str, Any] = {"status": outcome}
    if attempts is not None:
        entry["attempts"] = attempts
    outcomes[str(assertion_id)] = entry


def attempts_for(state: dict[str, Any], assertion_id: int) -> int:
    raw = state.get("outcomes", {}).get(str(assertion_id), {})
    return int(raw.get("attempts", 0))


__all__ = [
    "BackfillCountReport",
    "Kind",
    "SkipClass",
    "count_backfill_candidates",
    "enrichment_skip_reason",
    "events_array_len",
    "fetch_candidate_batch",
    "field_populated",
    "load_state",
    "pipeline_payload",
    "prospective_question_form_share",
    "prospective_starts_with_what",
    "record_outcome",
    "attempts_for",
    "save_state",
]
