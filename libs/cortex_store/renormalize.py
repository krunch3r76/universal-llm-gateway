"""T0 mechanical predicate-normalize adjudicator (Wave 1 graduation honesty).

Flag-fields-only writes: may update ``review_status``, ``reviewer``,
``reviewed_at``, ``review_notes`` — never ``predicate_form`` / claim text.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from predicate_form import NORMALIZER_VERSION, normalize_predicate_domain
from predicate_form.entity_resolve import DBEntityResolver

from .db import query
from .routes.assertions._shared import _flag_reasons_from_result

_PREDICATE_NORMALIZE_NOTE_LIKE = "%predicate normalize%"


def _infer_reason_from_normalize(normalize_result: dict) -> str:
    reasons = _flag_reasons_from_result(normalize_result)
    return reasons[0] if reasons else "legacy_literal"


def _infer_reason_from_notes(review_notes: str | None) -> str:
    if not review_notes:
        return "legacy_literal"
    for part in review_notes.split(";"):
        chunk = part.strip()
        if chunk.startswith("predicate normalize:"):
            remainder = chunk.removeprefix("predicate normalize:").strip()
            token = remainder.split(":", 1)[0].strip()
            return token or "legacy_literal"
    if "requires_human_review" in review_notes:
        return "legacy_literal"
    return "unknown"


def dry_run_stratify(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only guard re-fire over flagged predicate-normalize rows."""
    rows = query(
        conn,
        "SELECT id, entity_id, claim, predicate_form, review_notes "
        "FROM assertions WHERE superseded_by IS NULL "
        "AND review_status = 'flagged' "
        f"AND review_notes LIKE '{_PREDICATE_NORMALIZE_NOTE_LIKE}' "
        "AND predicate_form IS NOT NULL",
    )
    out: list[dict[str, Any]] = []
    resolver = DBEntityResolver(conn)
    for row in rows:
        assertion_id = int(row["id"])
        entity_id = str(row["entity_id"])
        predicate_form = str(row["predicate_form"])
        claim = str(row.get("claim") or "")
        try:
            normalize_result = normalize_predicate_domain(
                entity_id,
                predicate_form,
                claim_text=claim,
                resolver=resolver,
            )
            inferred = _infer_reason_from_normalize(normalize_result)
        except Exception:
            inferred = _infer_reason_from_notes(str(row.get("review_notes") or ""))
        out.append({"assertion_id": assertion_id, "inferred_reason": inferred})
    return out


def _selection_predicate(version: str = NORMALIZER_VERSION) -> tuple[str, tuple[str, ...]]:
    sql = (
        "SELECT id, entity_id, claim, predicate_form, review_notes, normalizer_version "
        "FROM assertions WHERE superseded_by IS NULL "
        "AND review_status = 'flagged' "
        f"AND review_notes LIKE '{_PREDICATE_NORMALIZE_NOTE_LIKE}' "
        "AND predicate_form IS NOT NULL "
        "AND COALESCE(normalizer_version, 'v0') < ?"
    )
    return sql, (version,)


def t0_adjudicate_flagged(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    sample_limit: int = 50,
    version: str = NORMALIZER_VERSION,
    record_event: Any | None = None,
) -> dict[str, Any]:
    """Mechanical clear for pre-bump flagged rows whose guards no longer fire."""
    stratify = dry_run_stratify(conn)
    before_count = len(stratify)
    sql, params = _selection_predicate(version)
    rows = query(conn, sql, params)
    resolver = DBEntityResolver(conn)
    cleared: list[int] = []
    by_reason: dict[str, int] = {}
    reviewer = f"normalizer:{version}"
    now = datetime.now(UTC).isoformat()

    for row in rows:
        assertion_id = int(row["id"])
        entity_id = str(row["entity_id"])
        predicate_form = str(row["predicate_form"])
        claim = str(row.get("claim") or "")
        try:
            normalize_result = normalize_predicate_domain(
                entity_id,
                predicate_form,
                claim_text=claim,
                resolver=resolver,
            )
        except Exception:
            continue
        if normalize_result.get("requires_human_review"):
            reason = _infer_reason_from_normalize(normalize_result)
            by_reason[reason] = by_reason.get(reason, 0) + 1
            continue
        reason = _infer_reason_from_normalize(normalize_result) or "cleared"
        by_reason[reason] = by_reason.get(reason, 0) + 1
        if dry_run:
            cleared.append(assertion_id)
            continue
        conn.execute(
            "UPDATE assertions SET review_status = 'committed', reviewer = ?, "
            "reviewed_at = ?, review_notes = CASE WHEN review_notes IS NOT NULL "
            "THEN review_notes || '; T0 mechanical clear' ELSE 'T0 mechanical clear' END "
            "WHERE id = ?",
            (reviewer, now, assertion_id),
        )
        cleared.append(assertion_id)
        if record_event is not None:
            record_event(
                "cortex.predicate.review.cleared",
                assertion_id=assertion_id,
                reviewer=reviewer,
                inferred_reason=reason,
            )

    if not dry_run:
        conn.commit()

    after_count = before_count - len(cleared) if not dry_run else before_count
    summary = {
        "before_count": before_count,
        "after_count": max(after_count, 0),
        "cleared_count": len(cleared),
        "by_reason": by_reason,
        "dry_run": dry_run,
        "sample_cleared_ids": cleared[:sample_limit],
        "stratify_snapshot": stratify,
    }
    if record_event is not None and not dry_run:
        record_event("cortex.predicate.renormalize.pass_complete", **summary)
    return summary


__all__ = ["dry_run_stratify", "t0_adjudicate_flagged"]
