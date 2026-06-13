"""GET /skills — seat-filtered skill manifest over HTTP (HTTP-first agent substrate PoC).

First non-boot consumer of the shared default-DENY seat gate
(`seat_applicability.FOR_AGENT_CLAUSE` + `canonical_seat_or_422`). Proves the B0
gate is reusable by a second route family with no duplication: same default-deny
semantics, same 422 slug validation, same canonical seat enum.

Serves the INDEX envelope — id, name, trigger, `source_uri` + body `digest`,
`applicable_agents` — so an agent with only `curl` can discover its seat-correct
skill set. Bodies stay pull-on-demand via `source_uri` (preserves the 1637 trim).
todo:skills-http-endpoint / decision:http-first-agent-substrate.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from ..confidence_field import lifecycle_not_value_sql_predicate
from ..db import cortex_conn
from ..db import query as db_query
from ..seat_applicability import (
    CAPABILITY_CLAUSE,
    FOR_AGENT_CLAUSE,
    canonical_seat_or_422,
    seat_capabilities_json,
)
from ._skill_index import (
    body_digest,
    entity_types_for_layer,
    index_envelope_fields,
    slug_from_row,
)
from .boot._skill_trigger import _resolve_skill_file, skill_trigger_text

_DEPRECATED_EXCLUDE = lifecycle_not_value_sql_predicate("deprecated")

router = APIRouter(prefix="/skills", tags=["skills"])

# Same default-DENY seat semantics as /boot-skills: a skill with no
# `applicable_agents` attribute is withheld from every seat; universal
# visibility requires an explicit `["*"]`. The filter clause is the shared
# B0 gate — see seat_applicability.FOR_AGENT_CLAUSE.
_SKILLS_MANIFEST_SQL = f"""
    SELECT id, name, description, source_uri,
           json_extract(attributes, '$.trigger_short') AS trigger_short,
           json_extract(attributes, '$.skill_category') AS skill_category,
           json_extract(attributes, '$.applicable_agents') AS applicable_agents_json,
           COALESCE(CAST(json_extract(attributes, '$.delivery_priority') AS INTEGER), 100)
               AS delivery_priority,
           json_extract(attributes, '$.delivery_criticality') AS delivery_criticality
    FROM entities
    WHERE type IN ({{type_placeholders}})
      AND {_DEPRECATED_EXCLUDE}
      {{for_agent_filter}}{{capability_filter}}
    ORDER BY name ASC
    LIMIT ?
"""


def _decode_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(v) for v in values] if isinstance(values, list) else []


def _manifest_row(row: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(row.get("id") or "")
    return {
        "id": entity_id,
        "name": row.get("name"),
        "trigger": skill_trigger_text(row),
        "skill_category": row.get("skill_category"),
        "applicable_agents": _decode_list(row.get("applicable_agents_json")),
        "delivery_priority": (
            100
            if row.get("delivery_priority") is None
            else int(row["delivery_priority"])
        ),
        "delivery_criticality": row.get("delivery_criticality"),
        **index_envelope_fields(row),
    }


@router.get("")
def get_skills(
    limit: int = Query(200, ge=1, le=500, description="Max manifest entries"),
    layer: Annotated[
        str,
        Query(description="Discovery layer: skills, rules, or all."),
    ] = "skills",
    for_agent: str | None = Query(
        None,
        description=(
            "Filter to skills whose `applicable_agents` list contains either "
            "`*` (universal) or this seat slug. Canonical seat slug (e.g. "
            "`claude-web`, `claude-cursor`, `cursor-sdk`); legacy spellings are "
            "normalized; unknown slugs return HTTP 422. Skills with no "
            "`applicable_agents` are withheld (default-deny)."
        ),
    ),
) -> dict[str, Any]:
    """Seat-filtered skill manifest INDEX over HTTP (bodies pull-on-demand).

    Reuses the shared B0 seat gate so a `curl`-only agent receives exactly its
    seat-correct skill set. Each item ships the body `source_uri` + `digest`;
    full bodies are fetched on demand, never inlined here.
    """
    entity_types = entity_types_for_layer(layer)
    type_placeholders = ", ".join("?" * len(entity_types))
    params: list[Any] = [*entity_types, "deprecated"]
    if for_agent:
        canonical = canonical_seat_or_422(for_agent)
        for_agent_filter = FOR_AGENT_CLAUSE
        capability_filter = CAPABILITY_CLAUSE
        params.append(canonical)
        params.append(seat_capabilities_json(canonical))
    else:
        for_agent_filter = ""
        capability_filter = ""
    params.append(limit)
    sql = _SKILLS_MANIFEST_SQL.format(
        type_placeholders=type_placeholders,
        for_agent_filter=for_agent_filter,
        capability_filter=capability_filter,
    )
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()
    items = [_manifest_row(r) for r in rows]
    return {
        "items": items,
        "for_agent": for_agent,
        "layer": layer,
        "count": len(items),
    }


@router.get("/body")
def get_skill_body(
    id: str = Query(..., description="Skill or rule entity id (e.g. agent_skill:slug)"),
    expected_digest: str | None = Query(
        None, description="Optional digest for drift detection (409 on mismatch)"
    ),
) -> dict[str, Any]:
    """Return the substantive skill/rule body with source_uri and content digest."""
    conn = cortex_conn()
    try:
        rows = db_query(
            conn,
            "SELECT id, name, source_uri, type FROM entities WHERE id = ? "
            "AND type IN ('agent_skill', 'rule')",
            (id,),
        )
    finally:
        conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Skill not found: {id}")
    row = rows[0]
    entity_id = str(row.get("id") or "")
    slug = slug_from_row(row)
    source_uri = row.get("source_uri")
    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Skill body not resolvable for {id}"
        )
    try:
        body_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=404, detail=f"Skill body not readable for {id}"
        ) from exc
    digest = body_digest(source_uri, slug)
    if expected_digest and digest and expected_digest != digest:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "digest_mismatch",
                "expected_digest": expected_digest,
                "digest": digest,
            },
        )
    return {
        "id": entity_id,
        "source_uri": source_uri,
        "digest": digest,
        "body": body_text,
    }
