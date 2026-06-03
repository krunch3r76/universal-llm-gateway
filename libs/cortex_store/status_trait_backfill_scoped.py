"""Post-052 scoped trait backfills (trait columns only)."""

from __future__ import annotations

import datetime
import sqlite3

from universal_logging import get_logger

from .db import table_exists
from .status_trait_read import entity_has_trait_columns
from .trait_vocabulary import TraitBackfillCounts, default_confidence_band_for_type

logger = get_logger("cortex-api.status_trait_backfill_scoped")


_ENTITY_HAS_LIVE_COMMITTED_ASSERTION = """
    EXISTS (
        SELECT 1 FROM assertions a
        WHERE a.entity_id = entities.id
          AND a.superseded_by IS NULL
          AND (a.review_status IS NULL OR a.review_status != 'staged')
    )
"""

_SCOPED_LIFECYCLE_ACTIVE_SQL = """
    SELECT id, type FROM entities
    WHERE lifecycle IS NULL
      AND id NOT IN (
          SELECT DISTINCT entity_id FROM assertions
          WHERE superseded_by IS NULL
            AND review_status = 'staged'
            AND entity_id IS NOT NULL
      )
"""

_SCOPED_CONFIDENCE_BAND_SQL = """
    SELECT id, type FROM entities
    WHERE confidence_band IS NULL
"""

_SCOPED_GRADUATED_LIFECYCLE_SQL = f"""
    SELECT id, type FROM entities
    WHERE lifecycle IS NULL
      AND {_ENTITY_HAS_LIVE_COMMITTED_ASSERTION.strip()}
"""


def count_graduated_null_lifecycle(conn: sqlite3.Connection) -> int:
    """Entities with ≥1 live committed assertion and NULL ``lifecycle``."""
    if not entity_has_trait_columns(conn):
        return 0
    if not table_exists(conn, "assertions"):
        row = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE lifecycle IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM entities WHERE lifecycle IS NULL AND {_ENTITY_HAS_LIVE_COMMITTED_ASSERTION.strip()}"
    ).fetchone()
    return int(row[0]) if row else 0


def count_null_confidence_band(conn: sqlite3.Connection) -> int:
    """Global NULL ``confidence_band`` count (post band backfill should be 0)."""
    if not entity_has_trait_columns(conn):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE confidence_band IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def run_scoped_confidence_band_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> TraitBackfillCounts:
    """Post-052: set conservative ``confidence_band`` on NULL rows (1172 T43 batch)."""
    if not entity_has_trait_columns(conn):
        logger.warning(
            "Trait columns absent — skipping scoped confidence_band backfill"
        )
        return TraitBackfillCounts()

    rows = conn.execute(_SCOPED_CONFIDENCE_BAND_SQL).fetchall()
    counts = TraitBackfillCounts()
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        entity_type = str(row["type"])
        band = default_confidence_band_for_type(entity_type)
        counts.entities_touched += 1
        counts.by_type[entity_type] = counts.by_type.get(entity_type, 0) + 1
        counts.confidence_band += 1
        if dry_run:
            logger.info(
                "dry-run scoped confidence_band backfill id=%s type=%s -> %s",
                row["id"],
                entity_type,
                band,
            )
            continue
        conn.execute(
            "UPDATE entities SET confidence_band = ?, updated_at = ? WHERE id = ?",
            (band, now, row["id"]),
        )

    if not dry_run and counts.entities_touched:
        conn.commit()
        logger.info(
            "Scoped confidence_band backfill committed: entities=%d band=%d",
            counts.entities_touched,
            counts.confidence_band,
        )
    return counts


def count_scoped_graduated_lifecycle_candidates(conn: sqlite3.Connection) -> int:
    """Count graduated entities with NULL ``lifecycle`` (1172 T45 batch)."""
    if not entity_has_trait_columns(conn):
        return 0
    if not table_exists(conn, "assertions"):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM ({_SCOPED_GRADUATED_LIFECYCLE_SQL.strip()})"
    ).fetchone()
    return int(row[0]) if row else 0


def run_scoped_graduated_lifecycle_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> TraitBackfillCounts:
    """Post-052: ``lifecycle='active'`` on graduated NULL rows (1172 T45 / 364 batch)."""
    if not entity_has_trait_columns(conn):
        logger.warning(
            "Trait columns absent — skipping scoped graduated lifecycle backfill"
        )
        return TraitBackfillCounts()

    rows = conn.execute(_SCOPED_GRADUATED_LIFECYCLE_SQL).fetchall()
    counts = TraitBackfillCounts()
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        counts.entities_touched += 1
        entity_type = str(row["type"])
        counts.by_type[entity_type] = counts.by_type.get(entity_type, 0) + 1
        counts.lifecycle += 1
        if dry_run:
            logger.info(
                "dry-run scoped graduated lifecycle backfill id=%s type=%s -> active",
                row["id"],
                entity_type,
            )
            continue
        conn.execute(
            "UPDATE entities SET lifecycle = 'active', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )

    if not dry_run and counts.entities_touched:
        conn.commit()
        logger.info(
            "Scoped graduated lifecycle backfill committed: entities=%d lifecycle=%d",
            counts.entities_touched,
            counts.lifecycle,
        )
    return counts


def count_scoped_lifecycle_active_candidates(conn: sqlite3.Connection) -> int:
    """Count entities eligible for committed-style lifecycle='active' backfill."""
    if not entity_has_trait_columns(conn):
        return 0
    if not table_exists(conn, "assertions"):
        row = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE lifecycle IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM ({_SCOPED_LIFECYCLE_ACTIVE_SQL.strip()})"
    ).fetchone()
    return int(row[0]) if row else 0


def run_scoped_lifecycle_active_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> TraitBackfillCounts:
    """Post-052: set ``lifecycle='active'`` on NULL rows without staged assertions.

    Matches 1172 T40 bucket: null lifecycle + no assertion with
    ``review_status='staged'``. Idempotent; does not touch ``confidence_band``.
    """
    if not entity_has_trait_columns(conn):
        logger.warning("Trait columns absent — skipping scoped lifecycle backfill")
        return TraitBackfillCounts()

    rows = conn.execute(_SCOPED_LIFECYCLE_ACTIVE_SQL).fetchall()
    counts = TraitBackfillCounts()
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        counts.entities_touched += 1
        entity_type = str(row["type"])
        counts.by_type[entity_type] = counts.by_type.get(entity_type, 0) + 1
        counts.lifecycle += 1
        if dry_run:
            logger.info(
                "dry-run scoped lifecycle backfill id=%s type=%s -> active",
                row["id"],
                entity_type,
            )
            continue
        conn.execute(
            "UPDATE entities SET lifecycle = 'active', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )

    if not dry_run and counts.entities_touched:
        conn.commit()
        logger.info(
            "Scoped lifecycle active backfill committed: entities=%d lifecycle=%d",
            counts.entities_touched,
            counts.lifecycle,
        )
    return counts
