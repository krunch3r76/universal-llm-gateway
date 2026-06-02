"""Shared eligibility for substantiation_sync and its shadow diff.

Phase 3 retarget writes ``confidence_band`` only (Φ* / credibility are separate:
D-core binary gate from non-superseded backing assertions, not host Ψ or
propagation). ``entities.status`` is untouched on the production path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .confidence_field import confidence_field, uses_confidence_band_axis
from .db import query
from .status_trait_read import effective_confidence_band, effective_lifecycle

# Confidence-axis status values (legacy overload). Lifecycle axis is caller-owned.
_CONFIDENCE_AXIS_STATUS = frozenset({"unsubstantiated", "confirmed", "provisional"})
_ADOPTION_STATUS_TYPES = frozenset({"decision"})


@dataclass(frozen=True)
class SubstantiationSyncScope:
    """Resolved write scope for one entity (no side effects)."""

    entity_id: str
    entity_type: str
    target_band: str | None
    current_band: str | None
    skip_reason: str | None
    demotion_blocked: bool = False


def is_confidence_band_demotion(current_band: str | None, target_band: str) -> bool:
    """Fail-closed demotion: stored band ``confirmed`` → D-core ``unsubstantiated`` only."""
    return current_band == "confirmed" and target_band == "unsubstantiated"


def resolve_substantiation_sync_scope(
    conn: sqlite3.Connection, entity_id: str
) -> SubstantiationSyncScope:
    """Return D-core target band and skip/demotion flags for *entity_id*."""
    from .dispatch_ops._detectors.substantiation import (
        CONFIRMED,
        derive_substantiation_state,
    )

    rows = query(
        conn,
        "SELECT type, lifecycle, confidence_band FROM entities WHERE id = ?",
        (entity_id,),
    )
    if not rows:
        return SubstantiationSyncScope(
            entity_id=entity_id,
            entity_type="",
            target_band=None,
            current_band=None,
            skip_reason="missing_entity",
        )
    row = rows[0]
    entity_type = row.get("type") or ""

    if not uses_confidence_band_axis(confidence_field(conn, entity_type)):
        return SubstantiationSyncScope(
            entity_id=entity_id,
            entity_type=entity_type,
            target_band=None,
            current_band=effective_confidence_band(row),
            skip_reason="non_status_confidence_field",
        )

    if effective_confidence_band(row) is None and effective_lifecycle(row) is not None:
        return SubstantiationSyncScope(
            entity_id=entity_id,
            entity_type=entity_type,
            target_band=None,
            current_band=effective_confidence_band(row),
            skip_reason="lifecycle_axis",
        )
    if entity_type in _ADOPTION_STATUS_TYPES:
        return SubstantiationSyncScope(
            entity_id=entity_id,
            entity_type=entity_type,
            target_band=None,
            current_band=effective_confidence_band(row),
            skip_reason="adoption_type",
        )

    derived = derive_substantiation_state(conn, entity_id)
    target = "confirmed" if derived == CONFIRMED else "unsubstantiated"
    current_band = effective_confidence_band(row)
    demotion = is_confidence_band_demotion(current_band, target)
    return SubstantiationSyncScope(
        entity_id=entity_id,
        entity_type=entity_type,
        target_band=target,
        current_band=current_band,
        skip_reason="demotion_blocked" if demotion else None,
        demotion_blocked=demotion,
    )


__all__ = [
    "SubstantiationSyncScope",
    "_ADOPTION_STATUS_TYPES",
    "_CONFIDENCE_AXIS_STATUS",
    "is_confidence_band_demotion",
    "resolve_substantiation_sync_scope",
]
