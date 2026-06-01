"""Derived substantiation state — Fork D (G1, thread 1173) D-core.

Confidence is DERIVED from backing assertions, not hand-set on the entity. This
module is the single canonical derivation used by the auditor-validatability
detector and (in full-D) by the materialized read-model. v1 is an auditable
deterministic rule, NOT fixed-point propagation — weights/propagation are added
only with signed edges + calibration data (sidecar gpt-5.5 refinement #8).

Enum: ``unsubstantiated / supported / confirmed / contested / refuted``.

D-core scope: derivation reads ALL non-superseded assertions on the entity. The
per-type BACKING-PREDICATE registry (only certain predicates substantiate — a
nickname must not confirm an entity, gpt-5.5 #3) and the contested/refuted
contradiction handling are full-D refinements and are deliberately minimal here.
"""

from __future__ import annotations

import sqlite3

from ...db import query

SubstantiationState = str  # one of the enum strings below

UNSUBSTANTIATED = "unsubstantiated"
SUPPORTED = "supported"
CONFIRMED = "confirmed"
CONTESTED = "contested"
REFUTED = "refuted"

# Confidence values that count as positive backing, strongest first.
_CONFIRMED_CONFIDENCE = "confirmed"
_SUPPORTING_CONFIDENCE = frozenset({"believed", "suspected"})


def derive_substantiation_state(
    conn: sqlite3.Connection, entity_id: str
) -> SubstantiationState:
    """Derive an entity's substantiation state from its backing assertions.

    Deterministic v1 rule:
      * ``confirmed``       — ≥1 non-superseded assertion at confidence
                              ``confirmed``.
      * ``supported``       — backing assertions exist at ``believed``/
                              ``suspected`` but none ``confirmed``.
      * ``unsubstantiated`` — no non-superseded backing assertions.

    ``contested`` / ``refuted`` are reserved in the enum but not emitted by the
    D-core rule (no contradiction predicate yet); full-D adds them.
    """
    rows = query(
        conn,
        "SELECT confidence, COUNT(*) AS n FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL "
        "GROUP BY confidence",
        (entity_id,),
    )
    counts = {r["confidence"]: r["n"] for r in rows}
    if counts.get(_CONFIRMED_CONFIDENCE, 0) > 0:
        return CONFIRMED
    if any(counts.get(c, 0) > 0 for c in _SUPPORTING_CONFIDENCE):
        return SUPPORTED
    return UNSUBSTANTIATED


__all__ = [
    "CONFIRMED",
    "CONTESTED",
    "REFUTED",
    "SUPPORTED",
    "UNSUBSTANTIATED",
    "derive_substantiation_state",
]
