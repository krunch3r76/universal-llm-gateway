"""Trait backfill — hybrid scope C and predicate-equivalence modes.

Hybrid (scope C): hot entity types only; maps legacy ``status`` → traits where
each trait is NULL. Never mutates ``status``. Idempotent.

Predicate-equivalence: all entity types; fills NULL traits wherever COALESCE
fallback predicates in ``confidence_field.py`` would match but trait-only would
miss (lifecycle, adoption_in, confidence_band). Idempotent.

Post-052 scoped backfills (NULL trait columns only) live in
``status_trait_backfill_scoped``. Legacy ``_run_trait_backfill`` retains a P0 fence
via :func:`require_entities_status_column` (exits 2 when ``entities.status`` is
absent). Use :func:`run_trait_completeness_scan` for read-only coverage on live DBs.
"""

from __future__ import annotations

import datetime
import sqlite3
import sys
from universal_logging import get_logger

from .db import table_exists
from .status_trait_read import entity_has_trait_columns
from .trait_vocabulary import (
    ADOPTION_VALUES,
    CONFIDENCE_BAND_VALUES,
    LIFECYCLE_VALUES,
    TraitBackfillCounts,
    TraitCompletenessCounts,
    default_confidence_band_for_type,
)

logger = get_logger("cortex-api.status_trait_backfill")

_ENTITIES_STATUS_DROPPED_MSG = (
    "entities.status dropped (migration 052); rewrite required (1172-E)"
)


def require_entities_status_column(conn: sqlite3.Connection) -> None:
    """Abort when migration 052 removed ``entities.status`` (post-DROP live DB)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
    if "status" not in cols:
        print(_ENTITIES_STATUS_DROPPED_MSG, file=sys.stderr)
        raise SystemExit(2)


# Operator scope C (2026-06-02): required hot types + optional plan-family.
HOT_TYPES_REQUIRED: frozenset[str] = frozenset({"todo", "decision", "agent_skill"})
HOT_TYPES_OPTIONAL: frozenset[str] = frozenset({"project", "plan", "plan_phase"})
HOT_TYPES_DEFAULT: frozenset[str] = HOT_TYPES_REQUIRED | HOT_TYPES_OPTIONAL

# Legacy decision status → adoption trait (workflow_coherence / Option C tests).
_DECISION_STATUS_TO_ADOPTION: dict[str, str] = {
    "provisional": "proposed",
    "confirmed": "adopted",
    "superseded": "superseded",
}


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

    if confidence_band is None and s in CONFIDENCE_BAND_VALUES:
        updates["confidence_band"] = s

    if lifecycle is None and s in LIFECYCLE_VALUES:
        updates["lifecycle"] = s

    if entity_type == "decision" and adoption is None:
        if s in _DECISION_STATUS_TO_ADOPTION:
            updates["adoption"] = _DECISION_STATUS_TO_ADOPTION[s]
        elif s in ADOPTION_VALUES:
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
        and s in LIFECYCLE_VALUES
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
    """Shared scan/update loop for hybrid and predicate-equivalence backfills.

    Calls :func:`require_entities_status_column` (P0 fence) — exits 2 on a
    post-052 DB.  The legacy ``SELECT … status …`` scan is retired; calling this
    on a post-DROP DB would exit before reaching the query.
    """
    require_entities_status_column(conn)
    if not entity_has_trait_columns(conn):
        logger.warning("Trait columns absent — skipping %s backfill", log_label)
        return TraitBackfillCounts()

    # Legacy scan path: retired — require_entities_status_column above exits 2
    # on post-052 DBs, so this query is never reached on a DROP-ed cortex.
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


def run_trait_completeness_scan(
    conn: sqlite3.Connection,
    *,
    types: frozenset[str] | None = None,
) -> TraitCompletenessCounts:
    """Read-only trait coverage scan for a post-052 DB (no ``entities.status``).

    Does NOT call :func:`require_entities_status_column` — safe to run after
    migration 052.  Returns null counts per trait column so the cert and
    operators can verify migration completeness without a write.

    ``types`` filters the scan to specific entity types; ``None`` scans all.
    """
    if not entity_has_trait_columns(conn):
        logger.warning("Trait columns absent — cannot run completeness scan")
        return TraitCompletenessCounts()

    if types is not None:
        type_list = sorted(types)
        placeholders = ",".join(["?"] * len(type_list))
        rows = conn.execute(
            f"SELECT id, type, confidence_band, lifecycle, adoption FROM entities "
            f"WHERE type IN ({placeholders})",
            tuple(type_list),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, type, confidence_band, lifecycle, adoption FROM entities"
        ).fetchall()

    counts = TraitCompletenessCounts(total=len(rows))
    for row in rows:
        entity_type = str(row["type"])
        counts.by_type[entity_type] = counts.by_type.get(entity_type, 0) + 1
        if row["confidence_band"] is None:
            counts.null_confidence_band += 1
        if row["lifecycle"] is None:
            counts.null_lifecycle += 1
        if entity_type == "decision" and row["adoption"] is None:
            counts.null_adoption_decisions += 1

    logger.info(
        "Trait completeness scan: total=%d null_band=%d null_lifecycle=%d null_adoption_decisions=%d",
        counts.total,
        counts.null_confidence_band,
        counts.null_lifecycle,
        counts.null_adoption_decisions,
    )
    return counts


from .status_trait_backfill_scoped import (
    count_graduated_null_lifecycle,
    count_null_confidence_band,
    count_scoped_graduated_lifecycle_candidates,
    count_scoped_lifecycle_active_candidates,
    run_scoped_confidence_band_backfill,
    run_scoped_graduated_lifecycle_backfill,
    run_scoped_lifecycle_active_backfill,
)


__all__ = [
    "HOT_TYPES_DEFAULT",
    "HOT_TYPES_OPTIONAL",
    "HOT_TYPES_REQUIRED",
    "_ENTITIES_STATUS_DROPPED_MSG",
    "TraitBackfillCounts",
    "TraitCompletenessCounts",
    "require_entities_status_column",
    "planned_predicate_equivalence_updates",
    "planned_trait_updates",
    "count_graduated_null_lifecycle",
    "count_null_confidence_band",
    "count_scoped_graduated_lifecycle_candidates",
    "count_scoped_lifecycle_active_candidates",
    "run_hybrid_trait_backfill",
    "run_scoped_confidence_band_backfill",
    "run_scoped_graduated_lifecycle_backfill",
    "run_predicate_equivalence_trait_backfill",
    "run_scoped_lifecycle_active_backfill",
    "run_trait_completeness_scan",
]
