"""Batch apply for substantiation_sync ``confidence_band`` promotions.

Uses shared ``substantiation_sync_gating`` eligibility; writes ``confidence_band``
only via ``recompute_entity_substantiation_status``. Idempotent when band already
matches target.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from universal_logging import get_logger

from .confidence_field import confidence_field, uses_confidence_band_axis
from .db import query
from .status_trait_read import entity_has_trait_columns
from .substantiation_sync import recompute_entity_substantiation_status
from .substantiation_sync_gating import resolve_substantiation_sync_scope

logger = get_logger("cortex-api.substantiation_sync_batch")


@dataclass
class SubstantiationSyncBatchCounts:
    """Per-run counters aligned with ``substantiation_sync_shadow`` scope buckets."""

    total_entities: int = 0
    promotions: int = 0
    unchanged: int = 0
    skipped_non_status_confidence_field: int = 0
    skipped_lifecycle_axis: int = 0
    skipped_adoption_type: int = 0
    skipped_missing_entity: int = 0
    demotions_blocked: int = 0

    @property
    def skipped(self) -> int:
        return (
            self.skipped_non_status_confidence_field
            + self.skipped_lifecycle_axis
            + self.skipped_adoption_type
            + self.skipped_missing_entity
        )


def run_substantiation_sync_batch(
    conn: sqlite3.Connection, *, dry_run: bool = True
) -> SubstantiationSyncBatchCounts:
    """Scan all entities; promote ``confidence_band`` where D-core target differs."""
    if not entity_has_trait_columns(conn):
        raise RuntimeError("confidence_band column missing — run migration 050 first")

    counts = SubstantiationSyncBatchCounts()
    entities = query(conn, "SELECT id, type FROM entities ORDER BY id")
    counts.total_entities = len(entities)

    for row in entities:
        eid = row["id"]
        etype = row.get("type") or ""

        if not uses_confidence_band_axis(confidence_field(conn, etype)):
            counts.skipped_non_status_confidence_field += 1
            continue

        scope = resolve_substantiation_sync_scope(conn, eid)
        if scope.skip_reason == "missing_entity":
            counts.skipped_missing_entity += 1
            continue
        if scope.skip_reason == "lifecycle_axis":
            counts.skipped_lifecycle_axis += 1
            continue
        if scope.skip_reason == "adoption_type":
            counts.skipped_adoption_type += 1
            continue
        if scope.demotion_blocked:
            counts.demotions_blocked += 1
            continue

        assert scope.target_band is not None
        if scope.target_band == scope.current_band:
            counts.unchanged += 1
            continue

        if dry_run:
            counts.promotions += 1
            continue

        if recompute_entity_substantiation_status(conn, eid) is not None:
            counts.promotions += 1

    if not dry_run and counts.promotions > 0:
        conn.commit()
        logger.info(
            "substantiation_sync batch committed promotions=%d unchanged=%d skipped=%d",
            counts.promotions,
            counts.unchanged,
            counts.skipped,
        )
    return counts


__all__ = ["SubstantiationSyncBatchCounts", "run_substantiation_sync_batch"]
