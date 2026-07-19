"""Chunk- and document-level co-occurrence filtering for hint terms."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from services.rag.corpus_hints.constants import DEFAULT_METADATA_DB_PATH
from services.rag.events.query import rag_corpus_hints_filter_failed

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)

__all__ = ["filter_hints_by_cooccurrence"]


def _build_word_boundary_conditions(
    query_terms: list[str],
) -> tuple[list[str], list[str]]:
    """Build word-boundary SQL conditions for query terms."""
    conditions: list[str] = []
    params: list[str] = []
    for term in query_terms:
        norm = term.lower().strip()
        if not norm:
            continue
        for prefix in ("prop.name@@", "prop.topic@@"):
            conditions.append("key = ?")
            params.append(f"{prefix}{norm}")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}{norm} %")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}% {norm}")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}% {norm} %")
    return conditions, params


def _chunk_level_overlap(
    conn: sqlite3.Connection, min_threshold: int
) -> dict[str, int]:
    """Join query_chunks ⋈ hint_chunks on (source, chunk_id); return key→count."""
    rows = conn.execute(
        "SELECT h.key, COUNT(DISTINCT h.chunk_id) AS overlap_count"
        " FROM hint_chunks h"
        " INNER JOIN query_chunks q ON h.source = q.source AND h.chunk_id = q.chunk_id"
        " GROUP BY h.key"
        " HAVING overlap_count >= ?",
        (min_threshold,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _doc_level_overlap(conn: sqlite3.Connection) -> dict[str, int]:
    """Fallback: join on source only (document-level), threshold=1."""
    rows = conn.execute(
        "SELECT h.key, COUNT(DISTINCT h.source) AS doc_overlap"
        " FROM hint_chunks h"
        " INNER JOIN query_chunks q ON h.source = q.source"
        " GROUP BY h.key"
        " HAVING doc_overlap >= 1",
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _order_hints_by_overlap(
    hint_terms: list[str], key_overlaps: dict[str, int]
) -> list[str]:
    """Map matched keys back to original hint terms, sorted by overlap desc."""
    term_scores: dict[str, int] = {}
    for key, count in key_overlaps.items():
        for prefix in ("prop.name@@", "prop.topic@@"):
            if key.startswith(prefix):
                term = key[len(prefix) :]
                term_scores[term] = max(term_scores.get(term, 0), count)

    original_term_map: dict[str, str] = {
        h.lower().strip(): h for h in hint_terms if h.strip()
    }

    scored: list[tuple[str, int]] = []
    for norm_term, score in term_scores.items():
        if norm_term in original_term_map:
            scored.append((original_term_map[norm_term], score))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return [h for h, _ in scored]


def filter_hints_by_cooccurrence(
    query_terms: list[str],
    hint_terms: list[str],
    db_path: Path | None = None,
    *,
    min_chunk_cooccurrence: int = 2,
    event_bus: EventBus | None = None,
) -> list[str]:
    """Return hint terms that co-occur with query terms at chunk level."""
    if not query_terms or not hint_terms:
        return []

    if db_path is None:
        db_path = DEFAULT_METADATA_DB_PATH
    if not db_path.exists():
        logger.debug("Property index DB not found at %s", db_path)
        return []

    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ) as conn:
            wb_conditions, wb_params = _build_word_boundary_conditions(query_terms)
            if not wb_conditions:
                return []

            hint_keys: list[str] = []
            for hint in hint_terms:
                norm = hint.lower().strip()
                if norm:
                    hint_keys.append(f"prop.name@@{norm}")
                    hint_keys.append(f"prop.topic@@{norm}")
            if not hint_keys:
                return []

            where_clause = " OR ".join(wb_conditions)
            try:
                conn.execute("DROP TABLE IF EXISTS query_chunks")
                conn.execute("DROP TABLE IF EXISTS hint_chunks")

                conn.execute(
                    "CREATE TEMP TABLE query_chunks AS"
                    " SELECT DISTINCT source, chunk_id FROM properties"
                    f" WHERE source != '' AND ({where_clause})",
                    wb_params,
                )

                key_ph = ",".join("?" for _ in hint_keys)
                conn.execute(
                    "CREATE TEMP TABLE hint_chunks AS"
                    " SELECT DISTINCT key, source, chunk_id FROM properties"
                    f" WHERE key IN ({key_ph})",
                    hint_keys,
                )

                key_overlaps = _chunk_level_overlap(conn, min_chunk_cooccurrence)

                if not key_overlaps:
                    key_overlaps = _doc_level_overlap(conn)

                if not key_overlaps:
                    return []

                return _order_hints_by_overlap(hint_terms, key_overlaps)
            finally:
                conn.execute("DROP TABLE IF EXISTS query_chunks")
                conn.execute("DROP TABLE IF EXISTS hint_chunks")
    except sqlite3.OperationalError as exc:
        logger.warning("Cannot open property index DB read-only: %s", db_path)
        if event_bus is not None:
            event_bus.publish_from_sync(rag_corpus_hints_filter_failed(error=str(exc)))
        return []
    except Exception as exc:
        logger.debug("Co-occurrence query failed", exc_info=True)
        if event_bus is not None:
            event_bus.publish_from_sync(rag_corpus_hints_filter_failed(error=str(exc)))
        return []
