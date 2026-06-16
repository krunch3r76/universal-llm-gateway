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
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from ..confidence_field import (
    DISCOVERABLE_SKILL_LIFECYCLE,
    discoverable_skill_lifecycle_sql_predicate,
)
from ..db import cortex_conn
from ..db import query as db_query
from ..event_publisher import (
    cortex_skill_suggest_called,
    cortex_skill_suggest_completed,
    cortex_skill_suggest_degraded,
    cortex_skill_suggest_failed,
)
from ..seat_applicability import (
    CAPABILITY_CLAUSE,
    canonical_seat_or_422,
    for_agent_filter_clause,
    seat_capabilities_json,
)
from ..skill_suggest_rank import apply_rerank, rerank_enabled_default
from ._skill_index import (
    body_digest,
    entity_types_for_layer,
    index_envelope_fields,
    slug_from_row,
)
from ._skill_suggest import run_stage_a
from .boot._skill_trigger import _resolve_skill_file, skill_trigger_text

_DISCOVERABLE_SKILL_LIFECYCLE = discoverable_skill_lifecycle_sql_predicate()

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
      AND {_DISCOVERABLE_SKILL_LIFECYCLE}
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
            "Filter to skills whose `applicable_agents` list contains this "
            "seat slug. Cursor/sdk seats also inherit universal `*`. Web and "
            "API seats require an explicit slug. Unknown slugs return HTTP "
            "422. Skills with no `applicable_agents` are withheld "
            "(default-deny). Only `lifecycle=active` skills are listed."
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
    params: list[Any] = [*entity_types, DISCOVERABLE_SKILL_LIFECYCLE]
    if for_agent:
        canonical = canonical_seat_or_422(for_agent)
        for_agent_filter = for_agent_filter_clause(canonical)
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
    include_non_active: Annotated[
        bool,
        Query(
            description=(
                "Maintenance/debug: return body for an inactive skill. "
                "Not a security boundary."
            ),
        ),
    ] = False,
) -> dict[str, Any]:
    """Return the substantive skill/rule body with source_uri and content digest."""
    conn = cortex_conn()
    try:
        rows = db_query(
            conn,
            "SELECT id, name, source_uri, type, lifecycle FROM entities WHERE id = ? "
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
    lifecycle = row.get("lifecycle")
    is_skill = row.get("type") == "agent_skill"
    discoverable = (not is_skill) or (lifecycle == DISCOVERABLE_SKILL_LIFECYCLE)
    if is_skill and not discoverable and not include_non_active:
        return {
            "id": entity_id,
            "lifecycle": lifecycle,
            "discoverable": False,
            "body": None,
            "reason": "inactive_lifecycle_withheld",
        }
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
        "lifecycle": lifecycle,
        "discoverable": discoverable,
    }


_CONTEXT_MAX = 16384


class SkillSuggestRequest(BaseModel):
    agent: str | None = None
    loaded: list[str]
    conversation_context: str | None = None
    limit: int = Field(default=8, ge=1, le=25)
    rerank: bool | None = None


def _validate_loaded_value(loaded: Any) -> list[str]:
    if not isinstance(loaded, list):
        raise HTTPException(
            status_code=422,
            detail={"code": "loaded_invalid", "message": "loaded must be a list"},
        )
    for item in loaded:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "loaded_invalid",
                    "message": "loaded elements must be strings",
                },
            )
    return loaded


def _parse_suggest_request(data: dict[str, Any]) -> SkillSuggestRequest:
    _validate_loaded_value(data.get("loaded"))
    try:
        return SkillSuggestRequest.model_validate(data)
    except ValidationError as exc:
        detail = exc.errors()[0] if exc.errors() else {}
        loc = detail.get("loc", ())
        if "limit" in loc:
            raise HTTPException(
                status_code=422,
                detail={"code": "validation_error", "message": str(exc)},
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _context_digest(context: str | None) -> tuple[int, str]:
    text = context or ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return len(text), digest


def _public_stage_a_result(result: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k != "stage_a_candidates"}
    return out


@router.post("/suggest")
async def post_skill_suggest(
    request: Request,
    x_cortex_transport: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Seat-gated deterministic skill delta suggestions with optional rerank."""
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    body = _parse_suggest_request(data)
    suggest_id = str(uuid.uuid4())
    transport = (x_cortex_transport or "http").strip() or "http"
    t0 = time.monotonic()

    if not body.agent or not str(body.agent).strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_required", "message": "agent is required"},
        )

    if body.conversation_context is not None and len(body.conversation_context) > _CONTEXT_MAX:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "context_too_large",
                "message": f"conversation_context exceeds {_CONTEXT_MAX} characters",
            },
        )

    ctx_len, ctx_sha = _context_digest(body.conversation_context)
    effective_rerank = (
        body.rerank if body.rerank is not None else rerank_enabled_default()
    )
    cortex_skill_suggest_called(
        suggest_id=suggest_id,
        agent=body.agent,
        transport=transport,
        context_len=ctx_len,
        context_sha256=ctx_sha,
        loaded_count=len(body.loaded),
        rerank_requested=bool(effective_rerank),
    )

    try:
        stage_a = run_stage_a(
            agent=body.agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
    except HTTPException:
        raise
    except Exception as exc:
        cortex_skill_suggest_failed(
            suggest_id=suggest_id,
            exc_type=type(exc).__name__,
            detail=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    result = _public_stage_a_result(stage_a)
    ranker_status = str(result.get("ranker_status") or "disabled")
    degraded_reason: str | None = None
    rank_execution_id: str | None = None

    ctx_present = bool((body.conversation_context or "").strip())
    if (
        effective_rerank
        and ctx_present
        and result.get("suggestions")
        and ranker_status != "skipped_no_context"
    ):
        result, ranker_status, degraded_reason, rank_execution_id = apply_rerank(
            stage_a_result=result,
            stage_a_candidates=stage_a.get("stage_a_candidates", []),
            conversation_context=body.conversation_context or "",
            loaded=body.loaded,
            limit=body.limit,
        )
    elif not effective_rerank and ranker_status != "skipped_no_context":
        result["ranker_status"] = "disabled"
        ranker_status = "disabled"

    latency_ms = int((time.monotonic() - t0) * 1000)
    if degraded_reason:
        cortex_skill_suggest_degraded(
            suggest_id=suggest_id,
            ranker_status=ranker_status,
            degraded_reason=degraded_reason,
            latency_ms=latency_ms,
        )

    cortex_skill_suggest_completed(
        suggest_id=suggest_id,
        agent=result["agent"],
        candidate_count=len(stage_a.get("stage_a_candidates", [])),
        suggested_count=result.get("count", 0),
        omitted_count=len(result.get("omitted", [])),
        ranker_status=ranker_status,
        latency_ms=latency_ms,
        rank_execution_id=rank_execution_id,
    )
    return result
