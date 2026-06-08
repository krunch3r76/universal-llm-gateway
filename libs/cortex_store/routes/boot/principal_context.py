"""GET /boot-principal-context — PII-safe principal projection for compact boot block."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...db import cortex_conn
from ...db import query as db_query
from .temporal import _TEMPORAL_ACTIVE_PREDICATE

router = APIRouter(tags=["boot"])

# F3 HARD allowlist: legal_matter:* only — never the raw person:* assertion stream.
_PRINCIPAL_ACTIVE_MATTERS_SQL = f"""
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.entity_id LIKE 'legal_matter:%'
      AND {_TEMPORAL_ACTIVE_PREDICATE}
    ORDER BY a.valid_until ASC
    LIMIT ?
"""


def _format_active_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "entity_id": r["entity_id"],
        "entity_name": r["entity_name"],
        "claim": r["claim"],
        "valid_from": r["valid_from"],
        "valid_until": r["valid_until"],
        "confidence": r["confidence"],
    }


@router.get("/boot-principal-context")
def get_boot_principal_context(
    principal: str = Query(
        ...,
        description="Principal entity_id (e.g. person:kaywan-mansubi)",
        min_length=3,
    ),
    active_limit: int = Query(
        5, ge=1, le=20, description="Max active legal-matter rows"
    ),
) -> dict[str, Any]:
    """PII-safe principal projection for the compact boot head block.

    Field 1: ``attributes.durable_identity`` on the principal entity ONLY —
    never the assertion stream (F2). Empty/unset → null (no fallback).

    Field 2: temporally active ``legal_matter:*`` assertions ONLY (F3 allowlist),
    filtered with the same predicate as ``GET /boot-temporal`` active rows (F4).
    """
    if not principal.startswith("person:"):
        raise HTTPException(
            status_code=422,
            detail="principal must be a person: entity_id",
        )

    conn = cortex_conn()
    try:
        entity_rows = db_query(
            conn,
            "SELECT id, name, type, attributes FROM entities WHERE id = ?",
            (principal,),
        )
        if not entity_rows:
            raise HTTPException(
                status_code=404, detail=f"entity not found: {principal}"
            )

        entity = entity_rows[0]
        if entity["type"] != "person":
            raise HTTPException(
                status_code=422,
                detail=f"principal must be type person, got {entity['type']!r}",
            )

        durable_identity: str | None = None
        attrs_raw = entity.get("attributes")
        if attrs_raw:
            try:
                attrs = (
                    json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
                )
            except (json.JSONDecodeError, TypeError):
                attrs = {}
            if isinstance(attrs, dict):
                raw = attrs.get("durable_identity")
                if isinstance(raw, str) and raw.strip():
                    durable_identity = raw.strip()

        active_rows = db_query(conn, _PRINCIPAL_ACTIVE_MATTERS_SQL, (active_limit,))
    finally:
        conn.close()

    active = [_format_active_row(r) for r in active_rows]
    return {
        "principal_id": principal,
        "principal_name": entity["name"],
        "durable_identity": durable_identity,
        "active_matters": active,
    }
