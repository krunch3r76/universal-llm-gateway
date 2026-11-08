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
MAX_PERMANENT_ATTEMPTS = 3
PAUSE_BACKOFF_CAP_S = 300.0

_TRANSIENT_BODY_MARKERS = (
    "503",
    "not loaded",
    "unavailable",
    "token counting failed",
)


class DispatchErrorClass(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass
class DispatchResult:
    ok: bool
    detail: str
    error_class: DispatchErrorClass | None = None

_QUESTION_PREFIX = re.compile(r"^\s*(what|when|why|how|which)\b", re.I)

_BOILERPLATE_GENERIC = re.compile(
    r"(?i)"
    r"(?:\bdownstream processes\b|"
    r"\bhow might\b.+\baffect\b|"
    r"\bwhat happens if\b(?!\s+[\w/:.-]{3,})|"
    r"\bfuture queries about\b(?!\s+[\w/:.-]{3,})|"
    r"\balso relevant when\b(?!\s+[\w/:.-]{3,}))"
)

_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "assertion",
        "claim",
        "entity",
        "future",
        "might",
        "relevant",
        "should",
        "their",
        "there",
        "these",
        "those",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
    }
)


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


def split_prospective_cues(text: str | None) -> list[str]:
    if not text or not text.strip():
        return []
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def claim_anchor_tokens(claim: str, entity_id: str | None = None) -> set[str]:
    anchors: set[str] = set()
    if entity_id:
        anchors.add(entity_id)
        if ":" in entity_id:
            anchors.add(entity_id.split(":", 1)[1])
    for match in re.finditer(r"[`'\"]?([\w][\w/:.-]{2,})[`'\"]?", claim):
        tok = match.group(1)
        if "/" in tok or ":" in tok or tok[:1].isupper():
            anchors.add(tok)
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{4,}", claim):
        low = word.lower()
        if low not in _STOPWORDS:
            anchors.add(word)
    return anchors


def cue_is_generic(cue: str, *, claim: str, entity_id: str | None = None) -> bool:
    if _BOILERPLATE_GENERIC.search(cue):
        return True
    anchors = claim_anchor_tokens(claim, entity_id)
    if not anchors:
        return False
    cue_norm = cue.lower().replace("-", "_")
    return not any(a.lower().replace("-", "_") in cue_norm for a in anchors)


def prospective_quality_metrics(
    rows: list[tuple[str | None, str, str | None]],
) -> tuple[float, float]:
    """Return (generic_cue_share, length_compliance_share).

    ``rows`` are (summary, claim, entity_id) tuples.
    """
    total_cues = 0
    generic_cues = 0
    length_ok = 0
    nonempty = 0
    for summary, claim, entity_id in rows:
        if not summary or not summary.strip():
            continue
        nonempty += 1
        cues = split_prospective_cues(summary)
        if len(cues) <= 2:
            length_ok += 1
        for cue in cues:
            total_cues += 1
            if cue_is_generic(cue, claim=claim, entity_id=entity_id):
                generic_cues += 1
    generic_share = generic_cues / total_cues if total_cues else 0.0
    length_share = length_ok / nonempty if nonempty else 0.0
    return generic_share, length_share


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


def classify_dispatch_error(
    *,
    status_code: int | None,
    body: str,
    exc: BaseException | None,
) -> DispatchErrorClass:
    if exc is not None:
        if isinstance(exc, (TimeoutError, OSError)):
            return DispatchErrorClass.TRANSIENT
        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name or "connect" in exc_name or "read" in exc_name:
            return DispatchErrorClass.TRANSIENT
        return DispatchErrorClass.PERMANENT
    if status_code is None:
        return DispatchErrorClass.PERMANENT
    if status_code in (502, 503, 504):
        return DispatchErrorClass.TRANSIENT
    body_low = (body or "").lower()
    if status_code == 500 and any(m in body_low for m in _TRANSIENT_BODY_MARKERS):
        return DispatchErrorClass.TRANSIENT
    if status_code >= 400:
        if any(m in body_low for m in _TRANSIENT_BODY_MARKERS):
            return DispatchErrorClass.TRANSIENT
        return DispatchErrorClass.PERMANENT
    return DispatchErrorClass.PERMANENT


def parse_dispatch_http(status_code: int, body: str) -> DispatchResult:
    if status_code < 400:
        return DispatchResult(ok=True, detail="OK")
    detail = f"HTTP {status_code} {body[:300]}"
    err = classify_dispatch_error(status_code=status_code, body=body, exc=None)
    return DispatchResult(ok=False, detail=detail, error_class=err)


def parse_dispatch_exception(exc: BaseException) -> DispatchResult:
    err = classify_dispatch_error(status_code=None, body="", exc=exc)
    return DispatchResult(ok=False, detail=f"ERR {exc}", error_class=err)


def is_transient_failure_record(entry: dict[str, Any]) -> bool:
    if entry.get("error_class") == DispatchErrorClass.TRANSIENT.value:
        return True
    detail = str(entry.get("last_error") or "")
    if not detail:
        return (
            entry.get("status") == "failed"
            and int(entry.get("attempts", 0)) >= MAX_PERMANENT_ATTEMPTS
        )
    return (
        classify_dispatch_error(status_code=500, body=detail, exc=None)
        == DispatchErrorClass.TRANSIENT
    )


def requeue_failed_outcomes(
    state: dict[str, Any],
    *,
    transient_only: bool,
) -> list[int]:
    outcomes = state.setdefault("outcomes", {})
    removed_ids: list[int] = []
    for key, entry in list(outcomes.items()):
        if entry.get("status") != "failed":
            continue
        if transient_only and not is_transient_failure_record(entry):
            continue
        del outcomes[key]
        removed_ids.append(int(key))
    if removed_ids:
        stats = state.setdefault("stats", {})
        stats["failed"] = max(0, int(stats.get("failed", 0)) - len(removed_ids))
    return removed_ids


def terminal_outcome_ids(state: dict[str, Any]) -> set[int]:
    terminals: set[int] = set()
    for key, entry in state.get("outcomes", {}).items():
        if entry.get("status") in ("ok", "failed"):
            terminals.add(int(key))
    return terminals


def fetch_rows_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT id, claim, entity_id, confidence, prospective_summary, events_json "
        f"FROM assertions WHERE id IN ({placeholders}) AND superseded_by IS NULL",
        ids,
    ).fetchall()
    out = [dict(r) for r in rows if enrichment_skip_reason(r["claim"]) is None]
    return sorted(out, key=lambda r: int(r["id"]))


def record_outcome(
    state: dict[str, Any],
    assertion_id: int,
    outcome: str,
    *,
    attempts: int | None = None,
    last_error: str | None = None,
    error_class: str | None = None,
) -> None:
    outcomes = state.setdefault("outcomes", {})
    entry: dict[str, Any] = {"status": outcome}
    if attempts is not None:
        entry["attempts"] = attempts
    if last_error:
        entry["last_error"] = last_error[:500]
    if error_class:
        entry["error_class"] = error_class
    outcomes[str(assertion_id)] = entry


def attempts_for(state: dict[str, Any], assertion_id: int) -> int:
    raw = state.get("outcomes", {}).get(str(assertion_id), {})
    return int(raw.get("attempts", 0))


__all__ = [
    "BackfillCountReport",
    "DispatchErrorClass",
    "DispatchResult",
    "MAX_PERMANENT_ATTEMPTS",
    "PAUSE_BACKOFF_CAP_S",
    "Kind",
    "SkipClass",
    "classify_dispatch_error",
    "fetch_rows_by_ids",
    "is_transient_failure_record",
    "parse_dispatch_exception",
    "parse_dispatch_http",
    "requeue_failed_outcomes",
    "terminal_outcome_ids",
    "count_backfill_candidates",
    "enrichment_skip_reason",
    "events_array_len",
    "fetch_candidate_batch",
    "field_populated",
    "load_state",
    "pipeline_payload",
    "claim_anchor_tokens",
    "cue_is_generic",
    "prospective_quality_metrics",
    "prospective_question_form_share",
    "prospective_starts_with_what",
    "split_prospective_cues",
    "record_outcome",
    "attempts_for",
    "save_state",
]
