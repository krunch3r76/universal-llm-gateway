"""Reaper — sweep ephemeral entities past TTL with low entrenchment.

Manual-only trigger initially. Soft-delete: sets valid_until on assertions,
status='reaped' on entity, retires edges. Source files are NOT deleted.

Reaping criteria (ALL must be true):
1. retention_policy = 'ephemeral'
2. last_accessed_at < NOW() - retention_ttl_days (TTL expired)
3. max entrenchment_score < threshold across active assertions
4. No inbound edges from non-ephemeral entities (1-hop protection)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from universal_logging import get_logger

from ..db import cortex_conn, query

logger = get_logger("cortex-api.reaper")
router = APIRouter(prefix="/reaper", tags=["reaper"])

_DEFAULT_TTL_DAYS = 30
_DEFAULT_ENTRENCHMENT_THRESHOLD = 0.05


@dataclass
class ReapCandidate:
    entity_id: str
    entity_name: str
    entity_type: str
    retention_ttl_days: int
    last_accessed_at: str | None
    days_since_access: int
    max_entrenchment: float
    active_assertion_count: int
    protected_by: str | None


def _find_candidates(
    conn: object,
    ttl_default: int,
    entrenchment_threshold: float,
) -> list[ReapCandidate]:
    """Find all ephemeral entities eligible for reaping consideration."""
    rows = query(
        conn,
        "SELECT id, name, type, retention_ttl_days, last_accessed_at, created_at "
        "FROM entities "
        "WHERE retention_policy = 'ephemeral' AND status != 'reaped'",
    )

    now = datetime.now(UTC)
    candidates: list[ReapCandidate] = []

    for row in rows:
        entity_id = row["id"]
        ttl = row["retention_ttl_days"] or ttl_default

        access_ts = row["last_accessed_at"] or row["created_at"]
        try:
            ts = datetime.fromisoformat(access_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            days_since = (now - ts).days
        except (ValueError, TypeError):
            days_since = 999

        if days_since < ttl:
            continue

        entrenchment_rows = query(
            conn,
            "SELECT MAX(COALESCE(entrenchment_score, 0.0)) AS max_e, "
            "COUNT(*) AS cnt FROM assertions "
            "WHERE entity_id = ? AND superseded_by IS NULL",
            (entity_id,),
        )
        max_e = entrenchment_rows[0]["max_e"] if entrenchment_rows else 0.0
        cnt = entrenchment_rows[0]["cnt"] if entrenchment_rows else 0

        if (max_e or 0.0) >= entrenchment_threshold:
            continue

        protector = _check_permanent_inbound(conn, entity_id)

        candidates.append(ReapCandidate(
            entity_id=entity_id,
            entity_name=row["name"],
            entity_type=row["type"],
            retention_ttl_days=ttl,
            last_accessed_at=row["last_accessed_at"],
            days_since_access=days_since,
            max_entrenchment=max_e or 0.0,
            active_assertion_count=cnt,
            protected_by=protector,
        ))

    return candidates


def _check_permanent_inbound(conn: object, entity_id: str) -> str | None:
    """Return the first non-ephemeral entity with an inbound edge, or None."""
    rows = query(
        conn,
        "SELECT se.from_node FROM session_edges se "
        "JOIN entities e ON e.id = se.from_node "
        "WHERE se.to_node = ? AND se.valid_until IS NULL "
        "AND e.retention_policy != 'ephemeral' AND e.status != 'reaped' "
        "LIMIT 1",
        (entity_id,),
    )
    if rows:
        return rows[0]["from_node"]
    rows = query(
        conn,
        "SELECT se.to_node FROM session_edges se "
        "JOIN entities e ON e.id = se.to_node "
        "WHERE se.from_node = ? AND se.valid_until IS NULL "
        "AND e.retention_policy != 'ephemeral' AND e.status != 'reaped' "
        "LIMIT 1",
        (entity_id,),
    )
    return rows[0]["to_node"] if rows else None


def _reap_entity(conn: object, entity_id: str, now_iso: str) -> dict[str, int]:
    """Soft-delete an entity: close assertions, set reaped, retire edges."""
    a_count = conn.execute(
        "UPDATE assertions SET valid_until = ? "
        "WHERE entity_id = ? AND superseded_by IS NULL AND valid_until IS NULL",
        (now_iso, entity_id),
    ).rowcount

    conn.execute(
        "UPDATE entities SET status = 'reaped', updated_at = ? WHERE id = ?",
        (now_iso, entity_id),
    )

    e_count = conn.execute(
        "UPDATE session_edges SET valid_until = ? "
        "WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL",
        (now_iso, entity_id, entity_id),
    ).rowcount

    return {"assertions_closed": a_count, "edges_retired": e_count}


@router.get("/preview")
def reaper_preview(
    ttl_default: int = Query(_DEFAULT_TTL_DAYS, ge=1, description="Default TTL in days"),
    entrenchment_threshold: float = Query(
        _DEFAULT_ENTRENCHMENT_THRESHOLD, ge=0.0, le=1.0
    ),
) -> dict[str, Any]:
    """Dry-run: show what the reaper would sweep without modifying anything."""
    conn = cortex_conn()
    try:
        candidates = _find_candidates(conn, ttl_default, entrenchment_threshold)
    finally:
        conn.close()

    reapable = [c for c in candidates if c.protected_by is None]
    protected = [c for c in candidates if c.protected_by is not None]

    def _fmt(c: ReapCandidate) -> dict[str, Any]:
        d: dict[str, Any] = {
            "entity_id": c.entity_id,
            "entity_name": c.entity_name,
            "entity_type": c.entity_type,
            "days_since_access": c.days_since_access,
            "ttl_days": c.retention_ttl_days,
            "max_entrenchment": c.max_entrenchment,
            "active_assertions": c.active_assertion_count,
        }
        if c.protected_by:
            d["protected_by"] = c.protected_by
        return d

    return {
        "would_reap": [_fmt(c) for c in reapable],
        "protected": [_fmt(c) for c in protected],
        "reapable_count": len(reapable),
        "protected_count": len(protected),
        "params": {
            "ttl_default": ttl_default,
            "entrenchment_threshold": entrenchment_threshold,
        },
    }


@router.post("/run")
def reaper_run(
    ttl_default: int = Query(_DEFAULT_TTL_DAYS, ge=1, description="Default TTL in days"),
    entrenchment_threshold: float = Query(
        _DEFAULT_ENTRENCHMENT_THRESHOLD, ge=0.0, le=1.0
    ),
) -> dict[str, Any]:
    """Execute the reaper: soft-delete eligible ephemeral entities."""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        candidates = _find_candidates(conn, ttl_default, entrenchment_threshold)
        reapable = [c for c in candidates if c.protected_by is None]

        results: list[dict[str, Any]] = []
        for c in reapable:
            counts = _reap_entity(conn, c.entity_id, now_iso)
            results.append({
                "entity_id": c.entity_id,
                "entity_name": c.entity_name,
                **counts,
            })
            logger.info(
                "Reaped %s (%s): %d assertions closed, %d edges retired",
                c.entity_id, c.entity_name,
                counts["assertions_closed"], counts["edges_retired"],
            )

        conn.commit()
    finally:
        conn.close()

    return {
        "reaped": results,
        "count": len(results),
        "skipped_protected": len(candidates) - len(reapable),
        "params": {
            "ttl_default": ttl_default,
            "entrenchment_threshold": entrenchment_threshold,
        },
    }
