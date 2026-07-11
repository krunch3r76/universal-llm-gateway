"""Phase 2 read cutover — Option C (display + traits).

``status`` on API read projections is a synthesized human-readable string
combining confidence_band, lifecycle, and adoption without conflating axes.
Trait keys are exposed separately when a value is present on the trait columns.

Axis-aware synthesis (Option A, ``decision:todo-status-display-axis-aware``):
types whose auditable confidence rides ``workflow_state`` lead the display
string with ``workflow_state`` instead of ``confidence_band``.
"""

from __future__ import annotations

import sqlite3

from .confidence_field import confidence_field as lookup_confidence_field
from .trait_vocabulary import (
    CONFIDENCE_BAND_VALUES,
    LIFECYCLE_VALUES,
    NON_LIVE_LIFECYCLE,
)

# Canonical non-live lifecycle set (alias preserves the local name used below).
_LIFECYCLE_LEGACY_STATUS = NON_LIVE_LIFECYCLE


def entity_has_trait_columns(conn: sqlite3.Connection) -> bool:
    """True when Phase 0 trait columns exist on ``entities`` (PRAGMA-safe)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
    return "lifecycle" in cols and "confidence_band" in cols


def effective_confidence_band(row: dict[str, object]) -> str | None:
    """Confidence band from ``confidence_band`` trait only."""
    band = row.get("confidence_band")
    return str(band) if band is not None else None


def effective_lifecycle(row: dict[str, object]) -> str | None:
    """Lifecycle from ``lifecycle`` trait only."""
    lifecycle = row.get("lifecycle")
    return str(lifecycle) if lifecycle is not None else None


def effective_adoption(row: dict[str, object]) -> str | None:
    """Adoption trait only."""
    adoption = row.get("adoption")
    return str(adoption) if adoption is not None else None


def resolve_display_confidence_field(
    row: dict[str, object],
    *,
    conn: sqlite3.Connection | None = None,
    confidence_field: str | None = None,
) -> str | None:
    """Resolve the auditable-confidence axis for display synthesis."""
    if confidence_field is not None:
        return confidence_field
    entity_type = row.get("type")
    if conn is not None and entity_type is not None:
        return lookup_confidence_field(conn, str(entity_type))
    return None


def synthesize_status_display(
    row: dict[str, object],
    *,
    conn: sqlite3.Connection | None = None,
    confidence_field: str | None = None,
) -> str | None:
    """Human-readable display: axis-aware band or workflow_state · lifecycle · adoption."""
    axis = resolve_display_confidence_field(
        row, conn=conn, confidence_field=confidence_field
    )
    parts: list[str] = []
    if axis == "workflow_state":
        workflow_state = row.get("workflow_state")
        if workflow_state is not None:
            parts.append(str(workflow_state))
    else:
        band = effective_confidence_band(row)
        if band is not None:
            parts.append(band)
    lifecycle = effective_lifecycle(row)
    if lifecycle is not None:
        parts.append(lifecycle)
    adoption = effective_adoption(row)
    if adoption is not None:
        parts.append(adoption)
    if parts:
        return " · ".join(parts)
    return None


def trait_keys_when_present(row: dict[str, object]) -> dict[str, str]:
    """Trait fields to attach on read when each axis resolves to a value."""
    out: dict[str, str] = {}
    band = effective_confidence_band(row)
    if band is not None:
        out["confidence_band"] = band
    lifecycle = effective_lifecycle(row)
    if lifecycle is not None:
        out["lifecycle"] = lifecycle
    adoption = effective_adoption(row)
    if adoption is not None:
        out["adoption"] = adoption
    return out


def apply_option_c_read_projection(
    row: dict[str, object],
    *,
    conn: sqlite3.Connection | None = None,
    confidence_field: str | None = None,
) -> dict[str, object]:
    """Return a copy with synthesized ``status`` and trait keys when present."""
    out = dict(row)
    out.update(trait_keys_when_present(row))
    out["status"] = synthesize_status_display(
        row, conn=conn, confidence_field=confidence_field
    )
    return out


def card_status_summary_option_c(
    entity: dict[str, object],
    *,
    conn: sqlite3.Connection | None = None,
    confidence_field: str | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """Card v0 ``status_summary`` core: synthesized display + traits + extras."""
    summary: dict[str, object] = {
        "status": synthesize_status_display(
            entity, conn=conn, confidence_field=confidence_field
        )
    }
    summary.update(trait_keys_when_present(entity))
    if extra:
        summary.update(extra)
    return {k: v for k, v in summary.items() if v is not None}


def project_status_field_value(
    row: dict[str, object],
    *,
    conn: sqlite3.Connection | None = None,
    confidence_field: str | None = None,
) -> str | None:
    """Value for a projected ``status`` field in list_entities ``fields=`` queries."""
    return synthesize_status_display(
        row, conn=conn, confidence_field=confidence_field
    )


def lifecycle_axis_status_value(value: str) -> bool:
    return value in _LIFECYCLE_LEGACY_STATUS or value in LIFECYCLE_VALUES


def confidence_axis_status_value(value: str) -> bool:
    return value in CONFIDENCE_BAND_VALUES


def prior_status_corrupt(
    prior: dict[str, object], valid_status: frozenset[str]
) -> bool:
    """True when stored ``status`` is outside the valid enum (repair escape hatch)."""
    prior_status = prior.get("status")
    return prior_status is not None and str(prior_status) not in valid_status


def prior_confidence_corrupt(
    prior: dict[str, object], valid_status: frozenset[str]
) -> bool:
    """Corrupt confidence axis: invalid ``status`` or trait band when columns exist."""
    if prior_status_corrupt(prior, valid_status):
        return True
    band = prior.get("confidence_band")
    if band is not None and str(band) not in CONFIDENCE_BAND_VALUES:
        return True
    return False
