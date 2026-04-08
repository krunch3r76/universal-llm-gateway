"""Migration 023: Entrenchment scoring — Kumiho K÷7/K÷8 formalization.

Adds entrenchment_score column to assertions table and backfills existing
assertions using the composite formula:
  entrenchment = salience_weight × confidence_rank × derivation_weight

Kumiho K÷7 (Superexpansion): lower-entrenchment beliefs contract first.
Kumiho K÷8 (Subexpansion): revision makes minimal changes — entrenchment
orders the priority.

Origin: Agent bus thread 453, Phase A5 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger("cortex-api.migration.023")

CONFIDENCE_RANK: dict[str, float] = {
    "confirmed": 1.0,
    "believed": 0.75,
    "suspected": 0.5,
    "hypothesized": 0.25,
}

DERIVATION_WEIGHT: dict[str, float] = {
    "direct": 1.0,
    "direct_observation": 1.0,
    "quotation": 0.95,
    "agent_observation": 0.9,
    "stated": 0.85,
    "inference": 0.8,
    "compression": 0.7,
    "commitment": 0.9,
    "other": 0.6,
}

BETA_PER_HOUR = 0.001  # ~29-day half-life


def _compute_score(
    confidence: str | None,
    derivation_type: str | None,
    observed_at: str | None,
    created_at: str | None,
    now: datetime,
) -> float:
    """Compute entrenchment for a single assertion row."""
    ts_str = observed_at or created_at
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_hours = max((now - ts).total_seconds() / 3600.0, 0.0)
        except (ValueError, TypeError):
            age_hours = 720.0  # fallback ~30 days
    else:
        age_hours = 720.0

    recency = math.exp(-BETA_PER_HOUR * age_hours)
    salience = recency  # access frequency not available at migration time
    conf = CONFIDENCE_RANK.get(confidence or "believed", 0.5)
    deriv = DERIVATION_WEIGHT.get(derivation_type or "other", 0.6)
    return round(salience * conf * deriv, 4)


def migrate(conn: sqlite3.Connection) -> None:
    # Add column (idempotent — tolerates duplicate)
    try:
        conn.execute("ALTER TABLE assertions ADD COLUMN entrenchment_score REAL")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise

    # Backfill all existing assertions
    rows = conn.execute(
        "SELECT id, confidence, derivation_type, observed_at, created_at "
        "FROM assertions WHERE entrenchment_score IS NULL"
    ).fetchall()

    if not rows:
        logger.info("Migration 023: no assertions need backfill")
        return

    now = datetime.now(UTC)
    batch: list[tuple[float, int]] = []
    for row in rows:
        score = _compute_score(
            row[1],  # confidence
            row[2],  # derivation_type
            row[3],  # observed_at
            row[4],  # created_at
            now,
        )
        batch.append((score, row[0]))  # (score, id)

    conn.executemany(
        "UPDATE assertions SET entrenchment_score = ? WHERE id = ?",
        batch,
    )

    logger.info(
        "Migration 023 (entrenchment_score): backfilled %d assertions", len(batch)
    )
