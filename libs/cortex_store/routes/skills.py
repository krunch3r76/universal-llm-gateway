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

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Query

from ..confidence_field import lifecycle_not_value_sql_predicate
from ..db import cortex_conn
from ..db import query as db_query
from ..seat_applicability import FOR_AGENT_CLAUSE, canonical_seat_or_422
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
           json_extract(attributes, '$.applicable_agents') AS applicable_agents_json
    FROM entities
    WHERE type = 'agent_skill'
      AND {_DEPRECATED_EXCLUDE}
      {{for_agent_filter}}
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


def _body_digest(source_uri: str | None, slug: str) -> str | None:
    """Content digest of the resolved skill body for the INDEX envelope.

    The agent fetches the body on demand via `source_uri`; the digest lets it
    cache and detect drift without carrying the body in context. None when the
    body is not resolvable on disk.
    """
    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def _manifest_row(row: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(row.get("id") or "")
    slug = str(row.get("name") or "").strip() or entity_id.removeprefix("agent_skill:")
    return {
        "id": entity_id,
        "name": row.get("name"),
        "trigger": skill_trigger_text(row),
        "source_uri": row.get("source_uri"),
        "digest": _body_digest(row.get("source_uri"), slug),
        "skill_category": row.get("skill_category"),
        "applicable_agents": _decode_list(row.get("applicable_agents_json")),
    }


@router.get("")
def get_skills(
    limit: int = Query(200, ge=1, le=500, description="Max manifest entries"),
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
    params: list[Any] = ["deprecated"]
    if for_agent:
        canonical = canonical_seat_or_422(for_agent)
        for_agent_filter = FOR_AGENT_CLAUSE
        params.append(canonical)
    else:
        for_agent_filter = ""
    params.append(limit)
    sql = _SKILLS_MANIFEST_SQL.format(for_agent_filter=for_agent_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()
    items = [_manifest_row(r) for r in rows]
    return {"items": items, "for_agent": for_agent, "count": len(items)}
