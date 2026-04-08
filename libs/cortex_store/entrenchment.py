"""Entrenchment scoring for assertions — Kumiho K÷7/K÷8 formalization.

Entrenchment = salience_weight × confidence_rank × derivation_weight

Where:
- salience_weight combines recency (exp decay, β=0.001/hr, ~29-day half-life)
  and entity access frequency (capped at 50 accesses)
- confidence_rank maps the 4-level confidence taxonomy to [0.25, 1.0]
- derivation_weight maps derivation types to [0.6, 1.0]

Higher entrenchment → belief is more resistant to contraction.
K÷7: lower-entrenchment beliefs contract first.
K÷8: revision makes minimal changes — only targeted belief affected.

Origin: Agent bus thread 453, Phase A5 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime

from .db import query

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

BETA_PER_HOUR = 0.001  # ~29-day half-life for entrenchment decay


def compute_entrenchment(
    confidence: str | None,
    derivation_type: str | None,
    observed_at: str | None,
    created_at: str | None,
    entity_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
) -> float:
    """Compute entrenchment score for a single assertion.

    When *conn* and *entity_id* are provided, access frequency from
    ``entity_access_log`` is included in the salience weight. Otherwise
    only recency contributes to salience.
    """
    if now is None:
        now = datetime.now(UTC)

    ts_str = observed_at or created_at
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_hours = max((now - ts).total_seconds() / 3600.0, 0.0)
        except (ValueError, TypeError):
            age_hours = 720.0
    else:
        age_hours = 720.0

    recency = math.exp(-BETA_PER_HOUR * age_hours)

    access_freq = 0.0
    if conn is not None and entity_id:
        rows = query(
            conn,
            "SELECT COUNT(*) AS cnt FROM entity_access_log WHERE entity_id = ?",
            (entity_id,),
        )
        if rows:
            access_freq = min(rows[0]["cnt"] / 50.0, 1.0)

    salience = (recency * 0.6) + (access_freq * 0.4)

    conf = CONFIDENCE_RANK.get(confidence or "believed", 0.5)
    deriv = DERIVATION_WEIGHT.get(derivation_type or "other", 0.6)

    return round(salience * conf * deriv, 4)
