"""Phase 3 write cutover (Option A) — trait-first entity mutations.

When Phase 0 trait columns exist, production writers set ``lifecycle``,
``confidence_band``, and (for ``decision``) ``adoption`` instead of mutating
``entities.status``. New rows leave ``status`` NULL; reads synthesize via
``status_trait_read`` (Option C).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .status_trait_read import entity_has_trait_columns

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
_PROVISIONAL_BIRTH_TYPES = frozenset({"decision"})


@dataclass(frozen=True)
class BirthTraits:
    """Resolved trait values for entity birth (and legacy status when needed)."""

    confidence_band: str | None
    lifecycle: str | None
    adoption: str | None
    legacy_status: str


def resolve_birth_traits(
    entity_type: str,
    caller_status: str | None,
    *,
    provisional_birth_types: frozenset[str] = _PROVISIONAL_BIRTH_TYPES,
) -> BirthTraits:
    """Map Fork D birth rules to trait columns + legacy ``status`` mirror."""
    default_band = (
        "provisional" if entity_type in provisional_birth_types else "unsubstantiated"
    )
    lifecycle: str | None = None
    band = default_band
    adoption: str | None = "proposed" if entity_type == "decision" else None

    if caller_status is not None and caller_status in _LIFECYCLE_VALUES:
        lifecycle = caller_status
    elif caller_status is not None and caller_status in _CONFIDENCE_BAND_VALUES:
        band = default_band

    legacy_status = lifecycle if lifecycle is not None else band
    return BirthTraits(
        confidence_band=band,
        lifecycle=lifecycle,
        adoption=adoption,
        legacy_status=legacy_status,
    )


def resolve_staged_entity_traits(proposed_status: str | None) -> BirthTraits:
    """Staging-add path: provisional band unless lifecycle-only status proposed."""
    if proposed_status in ("merged", "deprecated", "reaped"):
        return BirthTraits(
            confidence_band=None,
            lifecycle=proposed_status,
            adoption=None,
            legacy_status=proposed_status,
        )
    return BirthTraits(
        confidence_band="provisional",
        lifecycle=None,
        adoption=None,
        legacy_status="provisional",
    )


def transcript_birth_traits() -> BirthTraits:
    """Transcript entities: ``content_hash`` confidence axis → band confirmed."""
    return BirthTraits(
        confidence_band="confirmed",
        lifecycle=None,
        adoption=None,
        legacy_status="confirmed",
    )


def write_entity_reaped(conn: sqlite3.Connection, entity_id: str, now_iso: str) -> None:
    """Soft-delete lifecycle: trait ``lifecycle=reaped`` when columns exist."""
    if entity_has_trait_columns(conn):
        conn.execute(
            "UPDATE entities SET lifecycle = 'reaped', updated_at = ? WHERE id = ?",
            (now_iso, entity_id),
        )
    else:
        conn.execute(
            "UPDATE entities SET status = 'reaped', updated_at = ? WHERE id = ?",
            (now_iso, entity_id),
        )


def trait_insert_extras(
    conn: sqlite3.Connection, traits: BirthTraits
) -> tuple[list[str], list[object]]:
    """Extra INSERT columns/values after core entity fields (trait mode only)."""
    if not entity_has_trait_columns(conn):
        return [], []
    cols: list[str] = []
    vals: list[object] = []
    if traits.confidence_band is not None:
        cols.append("confidence_band")
        vals.append(traits.confidence_band)
    if traits.lifecycle is not None:
        cols.append("lifecycle")
        vals.append(traits.lifecycle)
    if traits.adoption is not None:
        cols.append("adoption")
        vals.append(traits.adoption)
    return cols, vals


def redirect_status_update_to_traits(
    conn: sqlite3.Connection,
    updates: dict[str, object],
) -> dict[str, object]:
    """Map a surviving ``status`` update to trait columns when cutover is on."""
    if not entity_has_trait_columns(conn):
        return updates
    incoming = updates.get("status")
    if incoming is None:
        return updates
    out = dict(updates)
    status_val = str(incoming)
    out.pop("status", None)
    if status_val in _LIFECYCLE_VALUES:
        out["lifecycle"] = status_val
    elif status_val in _CONFIDENCE_BAND_VALUES:
        out["confidence_band"] = status_val
    return out


__all__ = [
    "BirthTraits",
    "redirect_status_update_to_traits",
    "resolve_birth_traits",
    "resolve_staged_entity_traits",
    "trait_insert_extras",
    "transcript_birth_traits",
    "write_entity_reaped",
]
