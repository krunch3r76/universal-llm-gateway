"""Trait backfill — hybrid scope C and predicate-equivalence modes.

Hybrid (scope C): hot entity types only; maps legacy ``status`` → traits where
each trait is NULL. Never mutates ``status``. Idempotent.

Predicate-equivalence: all entity types; fills NULL traits wherever COALESCE
fallback predicates in ``confidence_field.py`` would match but trait-only would
miss (lifecycle, adoption_in, confidence_band). Idempotent.

Conventions mirror ``status_trait_read``, ``status_trait_write``, and
``entity_crud`` axis splits.
"""

from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass, field

from universal_logging import get_logger

from .status_trait_read import entity_has_trait_columns

logger = get_logger("cortex-api.status_trait_backfill")

# Operator scope C (2026-06-02): required hot types + optional plan-family.
HOT_TYPES_REQUIRED: frozenset[str] = frozenset({"todo", "decision", "agent_skill"})
HOT_TYPES_OPTIONAL: frozenset[str] = frozenset({"project", "plan", "plan_phase"})
HOT_TYPES_DEFAULT: frozenset[str] = HOT_TYPES_REQUIRED | HOT_TYPES_OPTIONAL

_CONFIDENCE_BAND_VALUES = frozenset({"unsubstantiated", "provisional", "confirmed"})
_LIFECYCLE_VALUES = frozenset(
    {
        "active",
        "superseded",
        "merged",
        "deprecated",
        "reaped",
        "invalidated",
        "dismissed",
    }
)
_ADOPTION_VALUES = frozenset({"proposed", "adopted", "superseded"})

# Legacy decision status → adoption trait (workflow_coherence / Option C tests).
_DECISION_STATUS_TO_ADOPTION: dict[str, str] = {
    "provisional": "proposed",
    "confirmed": "adopted",
    "superseded": "superseded",
}


@dataclass
class TraitBackfillCounts:
    """Per-trait write counts for a backfill run."""

    confidence_band: int = 0
    lifecycle: int = 0
    adoption: int = 0
    entities_touched: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


def planned_trait_updates(
    entity_type: str,
    status: str | None,
    *,
    confidence_band: str | None,
    lifecycle: str | None,
    adoption: str | None,
) -> dict[str, str]:
    """Return trait column updates to apply (empty if nothing to backfill)."""
    if status is None:
        return {}
    s = str(status)
    updates: dict[str, str] = {}

    if confidence_band is None and s in _CONFIDENCE_BAND_VALUES:
        updates["confidence_band"] = s

    if lifecycle is None and s in _LIFECYCLE_VALUES:
        updates["lifecycle"] = s

    if entity_type == "decision" and adoption is None:
        if s in _DECISION_STATUS_TO_ADOPTION:
            updates["adoption"] = _DECISION_STATUS_TO_ADOPTION[s]
        elif s in _ADOPTION_VALUES:
            updates["adoption"] = s

    return updates


def planned_predicate_equivalence_updates(
    entity_type: str,
    status: str | None,
    *,
    confidence_band: str | None,
    lifecycle: str | None,
    adoption: str | None,
) -> dict[str, str]:
    """Backfill NULL traits wherever COALESCE fallback would match trait-only miss."""
    updates = planned_trait_updates(
        entity_type,
        status,
        confidence_band=confidence_band,
        lifecycle=lifecycle,
        adoption=adoption,
    )
    if status is None:
        return updates
    s = str(status)

    # adoption_in debt: legacy status='confirmed' → adoption='adopted' (all types).
    if adoption is None and s == "confirmed" and "adoption" not in updates:
        updates["adoption"] = "adopted"

    # Decision completeness: lifecycle-only status overload still needs adoption trait.
    if (
        entity_type == "decision"
        and adoption is None
        and "adoption" not in updates
        and s in _LIFECYCLE_VALUES
    ):
        updates["adoption"] = "superseded"

    return updates


def _run_trait_backfill(
    conn: sqlite3.Connection,
    *,
    where_sql: str | None = None,
    where_params: tuple[object, ...] = (),
    plan_fn,
    dry_run: bool,
    log_label: str,
) -> TraitBackfillCounts:
    """Shared scan/update loop for hybrid and predicate-equivalence backfills."""
    if not entity_has_trait_columns(conn):
        logger.warning("Trait columns absent — skipping %s backfill", log_label)
        return TraitBackfillCounts()

    base = "SELECT id, type, status, confidence_band, lifecycle, adoption FROM entities"
    if where_sql is None:
        rows = conn.execute(base).fetchall()
    else:
        rows = conn.execute(f"{base} WHERE {where_sql}", where_params).fetchall()

    counts = TraitBackfillCounts()
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row in rows:
        entity_type = str(row["type"])
        updates = plan_fn(
            entity_type,
            row["status"],
            confidence_band=row["confidence_band"],
            lifecycle=row["lifecycle"],
            adoption=row["adoption"],
        )
        if not updates:
            continue

        counts.entities_touched += 1
        counts.by_type[entity_type] = counts.by_type.get(entity_type, 0) + 1
        if "confidence_band" in updates:
            counts.confidence_band += 1
        if "lifecycle" in updates:
            counts.lifecycle += 1
        if "adoption" in updates:
            counts.adoption += 1

        if dry_run:
            logger.info(
                "dry-run %s trait backfill id=%s type=%s updates=%s",
                log_label,
                row["id"],
                entity_type,
                updates,
            )
            continue

        sets = [f"{col} = ?" for col in updates]
        params = [*updates.values(), now, row["id"]]
        conn.execute(
            f"UPDATE entities SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            params,
        )

    if not dry_run:
        conn.commit()
        logger.info(
            "%s trait backfill committed: entities=%d band=%d lifecycle=%d adoption=%d",
            log_label,
            counts.entities_touched,
            counts.confidence_band,
            counts.lifecycle,
            counts.adoption,
        )
    return counts


def run_hybrid_trait_backfill(
    conn: sqlite3.Connection,
    *,
    types: frozenset[str] = HOT_TYPES_DEFAULT,
    dry_run: bool = True,
) -> TraitBackfillCounts:
    """Backfill NULL traits on *types* from legacy ``status``; never flip ``status``."""
    type_list = sorted(types)
    placeholders = ",".join(["?"] * len(type_list))
    return _run_trait_backfill(
        conn,
        where_sql=f"type IN ({placeholders})",
        where_params=tuple(type_list),
        plan_fn=planned_trait_updates,
        dry_run=dry_run,
        log_label="hybrid",
    )


def run_predicate_equivalence_trait_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> TraitBackfillCounts:
    """Backfill all entity types for predicate-equivalence with COALESCE readers."""
    return _run_trait_backfill(
        conn,
        where_sql=None,
        plan_fn=planned_predicate_equivalence_updates,
        dry_run=dry_run,
        log_label="predicate-equivalence",
    )


__all__ = [
    "HOT_TYPES_DEFAULT",
    "HOT_TYPES_OPTIONAL",
    "HOT_TYPES_REQUIRED",
    "TraitBackfillCounts",
    "planned_predicate_equivalence_updates",
    "planned_trait_updates",
    "run_hybrid_trait_backfill",
    "run_predicate_equivalence_trait_backfill",
]
