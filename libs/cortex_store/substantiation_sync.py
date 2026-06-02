"""Persist D-core confidence band onto entities (Phase 3 write side).

Fork D derivation (``derive_substantiation_state``) was read-only in the auditor
until substantiation_sync; Phase 3 retargets the write to ``confidence_band``
only. ``entities.status`` is not updated here — Φ* propagation and assertion
credibility (Ψ) are separate arcs; this hook applies the binary D-core gate
(≥1 non-superseded ``confirmed`` assertion ⇒ ``confirmed``, else
``unsubstantiated``).

Gates (shared with ``substantiation_sync_shadow``):
  * ``type_confidence_fields``: confidence axis must be ``confidence_band``
  * lifecycle-axis legacy ``status`` (merged/deprecated/reaped) — skip
  * ``decision`` types — adoption semantics, skip
  * fail-closed on band demotion (e.g. confirmed → unsubstantiated)

Called inside assert/supersede transactions; does not commit.
"""

from __future__ import annotations

import datetime
import sqlite3

from universal_logging import get_logger

from .status_trait_read import entity_has_trait_columns
from .substantiation_sync_gating import resolve_substantiation_sync_scope

logger = get_logger("cortex-api.substantiation_sync")


def recompute_entity_substantiation_status(
    conn: sqlite3.Connection, entity_id: str
) -> str | None:
    """Recompute and persist ``confidence_band`` from backing assertions.

    Returns the new band if it changed, else ``None``. No-op when out of scope,
    trait columns are missing, band already matches, or a demotion would be required
    (fail-closed — production path never lowers band rank).
    """
    if not entity_has_trait_columns(conn):
        logger.warning(
            "substantiation_sync skipped id=%s: confidence_band column missing",
            entity_id,
        )
        return None

    scope = resolve_substantiation_sync_scope(conn, entity_id)
    if scope.skip_reason:
        if scope.demotion_blocked:
            logger.info(
                "substantiation_sync demotion blocked id=%s band=%r target=%r",
                entity_id,
                scope.current_band,
                scope.target_band,
            )
        return None

    target = scope.target_band
    assert target is not None
    if target == scope.current_band:
        return None

    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE entities SET confidence_band = ?, updated_at = ? WHERE id = ?",
        (target, now, entity_id),
    )
    logger.info(
        "Recomputed confidence_band id=%s %r -> %r",
        entity_id,
        scope.current_band,
        target,
    )
    return target


__all__ = ["recompute_entity_substantiation_status"]
