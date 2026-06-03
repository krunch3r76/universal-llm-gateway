"""Candidate load and staged-disposition preflight for provenance reconstruct."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from reconstruct_constants import (
    CANDIDATE_SQL,
    EXPECTED_RECONSTRUCT_STAGED_COUNT,
    MARKER,
    STAGED_ONLY_WRONG_COUNT_HINT,
)
from reconstruct_models import Candidate
from reconstruct_uri import parse_uris


def assert_disposition_dry_run_count(
    n: int,
    *,
    expected: int = EXPECTED_RECONSTRUCT_STAGED_COUNT,
) -> None:
    """Abort when a dry-run row count indicates the wrong disposition filter."""
    if abs(n - STAGED_ONLY_WRONG_COUNT_HINT) <= 100:
        print(
            f"WRONG FILTER: dry-run count={n} ~ staged-only (~{STAGED_ONLY_WRONG_COUNT_HINT}); "
            f"must include reviewer={MARKER!r} (expected {expected})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if n != expected:
        print(
            f"reconstruct staged flag count {n} != expected {expected}",
            file=sys.stderr,
        )
        raise SystemExit(2)


def verify_reconstruct_staged_disposition_filter(
    conn: sqlite3.Connection,
    *,
    expected: int = EXPECTED_RECONSTRUCT_STAGED_COUNT,
) -> int:
    """Preflight count before batch PATCH/supersede on reconstruct staged flags."""
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM assertions WHERE superseded_by IS NULL "
            "AND review_status = 'staged' AND reviewer = ?",
            (MARKER,),
        ).fetchone()[0]
    )
    assert_disposition_dry_run_count(count, expected=expected)
    return count


def load_candidates(
    db_path: Path, entity_ids: list[str] | None, limit: int | None
) -> list[Candidate]:
    sql = CANDIDATE_SQL
    params: list[Any] = [MARKER, MARKER]
    if entity_ids:
        placeholders = ",".join("?" * len(entity_ids))
        sql += f" AND entity_id IN ({placeholders})"
        params.extend(entity_ids)
    sql += " ORDER BY entity_id, id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: list[Candidate] = []
    for r in rows:
        out.append(
            Candidate(
                id=int(r["id"]),
                entity_id=str(r["entity_id"]),
                claim=str(r["claim"]),
                evidence=str(r["evidence"] or ""),
                evidence_uris=parse_uris(r["evidence_uris"]),
                chunk_id=r["chunk_id"],
                derivation_type=str(r["derivation_type"] or ""),
                confidence=str(r["confidence"]),
            )
        )
    return out
