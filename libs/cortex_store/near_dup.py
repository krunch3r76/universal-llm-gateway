"""Near-duplicate detection for assertions via FTS5 + SequenceMatcher.

Runs as a non-blocking observability check after successful new assertion
inserts. Near-duplicates are recorded as graph metadata in
``near_duplicate_flags`` — they never block writes.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from difflib import SequenceMatcher
from typing import NamedTuple

from universal_logging import get_logger

logger = get_logger("cortex-api.near-dup")

DEDUP_SIMILARITY_THRESHOLD = 0.85
DEDUP_FTS_CANDIDATE_LIMIT = 10

_WORD_RE = re.compile(r"\w+")


class NearDupMatch(NamedTuple):
    existing_id: int
    score: float


def _build_fts_query(claim: str) -> str | None:
    """Extract significant words from claim text for FTS5 matching."""
    words = _WORD_RE.findall(claim.lower())
    significant = [w for w in words if len(w) > 2][:8]
    if not significant:
        return None
    return " OR ".join(f'"{w}"' for w in significant)


def check_near_duplicate(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    new_assertion_id: int,
    *,
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
    candidate_limit: int = DEDUP_FTS_CANDIDATE_LIMIT,
) -> NearDupMatch | None:
    """Check for near-duplicate assertions on the same entity.

    Uses FTS5 to find candidates, then SequenceMatcher for fuzzy scoring.
    Returns the best match above threshold, or None.
    """
    fts_query = _build_fts_query(claim)
    if not fts_query:
        return None

    try:
        rows = conn.execute(
            "SELECT a.id, a.claim "
            "FROM assertions a "
            "WHERE a.id IN ("
            "  SELECT rowid FROM assertions_fts WHERE assertions_fts MATCH ?"
            ") "
            "AND a.entity_id = ? "
            "AND a.superseded_by IS NULL "
            "AND a.valid_until IS NULL "
            "AND a.id != ? "
            "LIMIT ?",
            (fts_query, entity_id, new_assertion_id, candidate_limit),
        ).fetchall()
    except sqlite3.OperationalError:
        logger.warning("FTS5 query failed for near-dup check, skipping", exc_info=True)
        return None

    if not rows:
        return None

    claim_lower = claim.lower()
    best_id: int | None = None
    best_score = 0.0

    for row in rows:
        candidate_claim: str = row[1]
        ratio = SequenceMatcher(None, claim_lower, candidate_claim.lower()).ratio()
        if ratio >= threshold and ratio > best_score:
            best_score = ratio
            best_id = row[0]

    if best_id is None:
        return None

    return NearDupMatch(existing_id=best_id, score=round(best_score, 4))


def record_near_duplicate(
    conn: sqlite3.Connection,
    assertion_id: int,
    duplicate_of: int,
    score: float,
) -> None:
    """Record a near-duplicate flag between two assertions."""
    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn.execute(
            "INSERT INTO near_duplicate_flags (assertion_id, duplicate_of, score, created_at) "
            "VALUES (?, ?, ?, ?)",
            (assertion_id, duplicate_of, score, now),
        )
        conn.commit()
        logger.info(
            "Near-duplicate flagged: assertion %d ≈ assertion %d (score=%.4f)",
            assertion_id,
            duplicate_of,
            score,
        )
    except sqlite3.OperationalError:
        logger.warning(
            "Failed to record near-duplicate flag (table may not exist yet)",
            exc_info=True,
        )
