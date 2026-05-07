from __future__ import annotations

import datetime
import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..action_hints import detect_expired_unresolved
from ..compaction import (
    POINTER_SQL_LIKE,
    SUMMARY_SQL_LIKE,
    apply_compaction_filter,
    is_tombstone_only,
    synthesize_predicate_summary,
)
from ..db import cortex_conn, decode_row, execute, json_encode, query
from ..dispatch_ops._shared import record
from ..models import (
    AssertionItem,
    CardAssertion,
    CardDebug,
    CardEdgeTypeCount,
    CardSection,
    CompactionProjection,
    EdgeItem,
    EntityCard,
    EntityCreate,
    EntityDetail,
    EntityIntent,
    EntityList,
    EntitySummary,
    EntityUpdate,
    RelationshipItem,
)
from .assertions import _ASSERTION_COLS
from .edges import _EDGE_COLS

logger = logging.getLogger("cortex-api.entities")
router = APIRouter(prefix="/entities", tags=["entities"])


def _workflow_schema(
    conn: sqlite3.Connection, entity_type: str
) -> dict[str, object] | None:
    """Fetch the workflow schema for *entity_type* if registered, else None."""
    rows = query(
        conn,
        "SELECT enum_values, initial_state, terminal_states "
        "FROM workflow_schemas WHERE entity_type = ?",
        (entity_type,),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "enum_values": json.loads(row["enum_values"]),
        "initial_state": row["initial_state"],
        "terminal_states": (
            json.loads(row["terminal_states"]) if row["terminal_states"] else None
        ),
    }


def _validate_workflow_state(
    conn: sqlite3.Connection, entity_type: str, value: str
) -> None:
    """Reject *value* if entity_type has a registered enum that excludes it.

    Types without a registered schema accept any value (free-form).
    """
    schema = _workflow_schema(conn, entity_type)
    if schema is None:
        return
    enum_values = schema["enum_values"]
    assert isinstance(enum_values, list)
    if value not in enum_values:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid workflow_state {value!r} for type {entity_type!r}. "
                f"Must be one of: {enum_values}"
            ),
        )


def _list_entities_impl(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    workflow_state: str | None = None,
    limit: int = 50,
    for_agent: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    clauses: list[str] = []
    params: list[object] = []

    if entity_type:
        clauses.append("type = ?")
        params.append(entity_type)

    if workflow_state is not None:
        clauses.append("workflow_state = ?")
        params.append(workflow_state)

    if for_agent:
        # applicable_agents is a JSON list attribute that names the agent
        # slugs that should see the entity. NULL / missing → treated as
        # universal via COALESCE so pre-backfill behaviour is "include".
        # Currently used by agent_skill but the filter is generic — any
        # entity type carrying applicable_agents participates.
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each("
            "COALESCE(json_extract(attributes, '$.applicable_agents'), "
            "json_array('*'))) WHERE value IN ('*', ?))"
        )
        params.append(for_agent)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT id, type, name, description, status, workflow_state, content_hash, "
        f"created_at FROM entities{where} ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)
    rows = query(conn, sql, tuple(params))
    return {"items": [EntitySummary(**row).model_dump() for row in rows]}


@router.get("", response_model=EntityList)
def list_entities(
    type: str | None = None,
    workflow_state: str | None = Query(
        None,
        description="Filter by typed workflow_state column (replaces the "
        "json_extract(attributes,'$.status') pattern).",
    ),
    limit: int = Query(50, ge=1, le=500),
    for_agent: str | None = Query(
        None,
        description=(
            "Filter to entities whose `applicable_agents` JSON-list "
            "attribute contains either `*` (universal) or this agent "
            "slug. Entities without the attribute are treated as "
            "universal. Currently consumed by agent_skill boot pulls "
            "but the filter is generic across all types."
        ),
    ),
) -> EntityList:
    """List entities, optionally constrained to one entity type / workflow_state."""
    with cortex_conn() as conn:
        data = _list_entities_impl(
            conn,
            entity_type=type,
            workflow_state=workflow_state,
            limit=limit,
            for_agent=for_agent,
        )
    return EntityList(items=[EntitySummary(**item) for item in data["items"]])


_ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})
_ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})

_RELATIONSHIP_SELECT = """
    r.id, r.from_entity AS source_id, r.to_entity AS target_id,
    r.type AS type_id, rt.description AS type_name,
    se.name AS source_name, te.name AS target_name,
    r.role, r.strength, r.evidence, r.chunk_id,
    r.valid_from, r.valid_until, r.source_uri,
    r.session_id, r.agent, r.created_at
"""

_RELATIONSHIP_FROM = """
    FROM relationships r
    JOIN relationship_types rt ON rt.type = r.type
    LEFT JOIN entities se ON se.id = r.from_entity
    LEFT JOIN entities te ON te.id = r.to_entity
"""


def _get_entity_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    include_edges: bool = False,
    edge_limit: int = 20,
    source: str = "agent",
    agent: str = "web",
    session_id: str | None = None,
    include_compaction_pointers: bool = False,
) -> dict[str, object]:
    entities = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not entities:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )
    entity = entities[0]

    assertion_rows = query(
        conn,
        f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
        "ORDER BY created_at DESC",
        (entity_id,),
    )

    rel_rows = query(
        conn,
        f"SELECT {_RELATIONSHIP_SELECT} {_RELATIONSHIP_FROM} "
        "WHERE (r.from_entity = ? OR r.to_entity = ?) AND r.active = 1 "
        "ORDER BY r.created_at DESC",
        (entity_id, entity_id),
    )

    edge_rows: list[dict] = []
    if include_edges:
        edge_rows = query(
            conn,
            f"SELECT {_EDGE_COLS} FROM session_edges "
            "WHERE (from_node = ? OR to_node = ?) "
            "AND valid_until IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (entity_id, entity_id, edge_limit),
        )

    if source != "boot":
        try:
            conn.execute(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source, session_id) "
                "VALUES (?, ?, 'entity_get', ?, ?)",
                (entity_id, agent, source, session_id),
            )
            conn.commit()
        except Exception:
            logger.warning("Access log insert failed for %s", entity_id)

    assertions: list[AssertionItem] = []
    for row in assertion_rows:
        try:
            assertions.append(AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )

    # §6.10 compaction-aware projection (Tier 0 — deterministic, no model)
    compaction_projection: CompactionProjection | None = None
    raw_dicts = [a.model_dump(mode="json") for a in assertions]
    archives_to_children: list[str] | None = None
    try:
        arc_rows = query(
            conn,
            "SELECT to_entity FROM relationships "
            "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
            (entity_id,),
        )
        archives_to_children = [r["to_entity"] for r in arc_rows]
    except Exception:
        logger.warning("archives_to lookup failed for %s", entity_id)
    projected_dicts, proj_meta = apply_compaction_filter(
        raw_dicts,
        include_compaction_pointers=include_compaction_pointers,
        archives_to_children=archives_to_children,
    )
    if proj_meta is not None:
        assertions = [AssertionItem(**d) for d in projected_dicts]
        compaction_projection = CompactionProjection(**proj_meta)

    relationships = [RelationshipItem(**row) for row in rel_rows]
    edges = [EdgeItem(**row) for row in edge_rows]
    hints = detect_expired_unresolved([a.model_dump() for a in assertions])
    return EntityDetail(
        **decode_row(entity, _ENTITY_JSON_FIELDS),
        assertions=assertions,
        relationships=relationships,
        reasoning_edges=edges,
        action_hints=hints or None,
        compaction_projection=compaction_projection,
    ).model_dump(mode="json")


_CARD_TOP_K_DEFAULT = 7
_CARD_INTENTS_DEFERRED = {"cluster", "impact"}


def _get_entity_card_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    top_k: int = _CARD_TOP_K_DEFAULT,
    debug: bool = False,
    source: str = "agent",
    agent: str = "web",
    session_id: str | None = None,
) -> dict[str, object]:
    """Card v0 read path (v2.4 §6.2 / §6.3).

    Projection-aware fetch plan: identity columns + top-K active assertions
    + relationship-type aggregates + archives_to count + section counts.
    NOT a load-and-trim wrapper over `_get_entity_impl` — that would
    violate the §6.2 architectural target (shrinking wire bytes without
    shrinking the fetch plan is not the win).

    Compaction-pointer ordering (§6.10) is honored at the SQL level: the
    archive-summary rows surface ahead of compaction pointers and other
    active assertions in the top-K.
    """
    rows_materialized = 0

    ent_rows = query(
        conn,
        "SELECT id, type, name, description, status, workflow_state, "
        "created_at, updated_at FROM entities WHERE id = ?",
        (entity_id,),
    )
    rows_materialized += len(ent_rows)
    if not ent_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )
    e = ent_rows[0]

    # §6.10 ordering: archive summaries first, compaction pointers last.
    # Parametrized via canonical SUMMARY_SQL_LIKE / POINTER_SQL_LIKE constants
    # from compaction.py — single source of truth (DRY, case-insensitive via
    # LOWER() on both sides). Active rows only — superseded payload is not
    # part of Card v0 (§6.2: avoid wholesale loading of superseded).
    a_rows = query(
        conn,
        "SELECT id, claim, confidence, derivation_type, valid_from, "
        "observed_at FROM assertions WHERE entity_id = ? "
        "AND superseded_by IS NULL "
        "ORDER BY "
        "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 0 ELSE 1 END) ASC, "
        "  (CASE WHEN LOWER(claim) LIKE LOWER(?) THEN 1 ELSE 0 END) ASC, "
        "  created_at DESC LIMIT ?",
        (entity_id, SUMMARY_SQL_LIKE, POINTER_SQL_LIKE, top_k),
    )
    rows_materialized += len(a_rows)

    count_rows = query(
        conn,
        "SELECT "
        "  SUM(CASE WHEN superseded_by IS NULL THEN 1 ELSE 0 END) AS active_n, "
        "  SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS superseded_n "
        "FROM assertions WHERE entity_id = ?",
        (entity_id,),
    )
    rows_materialized += len(count_rows)
    active_n = int(count_rows[0]["active_n"] or 0) if count_rows else 0
    superseded_n = int(count_rows[0]["superseded_n"] or 0) if count_rows else 0

    et_rows = query(
        conn,
        "SELECT type AS type_id, COUNT(*) AS n FROM relationships "
        "WHERE (from_entity = ? OR to_entity = ?) AND active = 1 "
        "GROUP BY type ORDER BY n DESC",
        (entity_id, entity_id),
    )
    rows_materialized += len(et_rows)
    rel_total = sum(int(r["n"]) for r in et_rows)

    arc_rows = query(
        conn,
        "SELECT to_entity FROM relationships "
        "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
        (entity_id,),
    )
    rows_materialized += len(arc_rows)
    archives_to_count = len(arc_rows)
    archives_to_children = [str(r["to_entity"]) for r in arc_rows]

    se_rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM session_edges "
        "WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL",
        (entity_id, entity_id),
    )
    rows_materialized += len(se_rows)
    edges_n = int(se_rows[0]["n"]) if se_rows else 0

    if source != "boot":
        try:
            conn.execute(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source, session_id) "
                "VALUES (?, ?, 'entity_get', ?, ?)",
                (entity_id, agent, source, session_id),
            )
            conn.commit()
        except Exception:
            logger.warning("Access log insert failed for %s (card)", entity_id)

    section_manifest = [
        CardSection(id="assertions", label="Assertions (active)", count=active_n),
        CardSection(
            id="assertions_superseded",
            label="Assertions (superseded)",
            count=superseded_n,
        ),
        CardSection(id="relationships", label="Relationships", count=rel_total),
        CardSection(id="archives_to", label="Archives", count=archives_to_count),
        CardSection(id="reasoning_edges", label="Reasoning edges", count=edges_n),
    ]

    # §6.10 tombstone-collapse: if all active assertions are compaction pointers
    # the operative content lives in a (possibly superseded) consolidation summary.
    # Fetch that summary and substitute it as the sole top-K entry so the card
    # shows meaningful content rather than a list of pointer claims.
    all_active_claims = [str(r["claim"]) for r in a_rows]
    if active_n > 0 and is_tombstone_only(all_active_claims):
        summary_rows = query(
            conn,
            "SELECT id, claim, confidence, derivation_type, valid_from, observed_at "
            "FROM assertions WHERE entity_id = ? "
            "AND LOWER(claim) LIKE LOWER(?) ORDER BY created_at DESC LIMIT 1",
            (entity_id, SUMMARY_SQL_LIKE),
        )
        rows_materialized += len(summary_rows)
        top_k_for_card = [
            CardAssertion(
                id=int(r["id"]),
                claim=str(r["claim"]),
                confidence=r["confidence"],
                derivation_type=r.get("derivation_type"),
                valid_from=r.get("valid_from"),
                observed_at=r.get("observed_at"),
            )
            for r in summary_rows
        ]
        predicate_summary: str | None = (
            f"archived → see children [{', '.join(archives_to_children)}]"
            if archives_to_children
            else "tombstoned"
        )
    else:
        top_k_for_card = [
            CardAssertion(
                id=int(r["id"]),
                claim=str(r["claim"]),
                confidence=r["confidence"],
                derivation_type=r.get("derivation_type"),
                valid_from=r.get("valid_from"),
                observed_at=r.get("observed_at"),
            )
            for r in a_rows
        ]
        # §6.3 / §6.7 heuristic fallback: deterministic edge-derived summary.
        # Populated from already-fetched relationship aggregates — no extra query,
        # no LLM. Returns empty string when the entity has no edges (never None).
        # Map SQL alias `n` → `count` to match synthesize_predicate_summary contract.
        predicate_summary = synthesize_predicate_summary(
            et_type_counts=[
                {"type_id": str(r["type_id"]), "count": int(r["n"])} for r in et_rows
            ],
            archives_to_children=archives_to_children,
        )

    card = EntityCard(
        id=str(e["id"]),
        type=str(e["type"]),
        name=str(e["name"]),
        summary_row=e.get("description"),
        status_summary={
            "status": e.get("status"),
            "workflow_state": e.get("workflow_state"),
            "updated_at": e.get("updated_at"),
        },
        top_k_assertions=top_k_for_card,
        edge_type_summary=[
            CardEdgeTypeCount(type_id=str(r["type_id"]), count=int(r["n"]))
            for r in et_rows
        ],
        archives_to_count=archives_to_count,
        section_manifest=section_manifest,
        predicate_summary=predicate_summary,
        freshness={
            "created_at": str(e["created_at"]),
            "updated_at": str(e["updated_at"]),
        },
        debug=CardDebug(fetch_plan_row_volume=rows_materialized) if debug else None,
    )
    return card.model_dump(mode="json")


@router.get("/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
    intent: EntityIntent = Query(
        "full",
        description=(
            "v2.4 §6.1 read intent. `full` (default) preserves the existing "
            "EntityDetail payload. `card` returns Card v0 (§6.3) via a "
            "projection-aware fetch plan. `cluster` and `impact` are "
            "reserved in the surface but not implemented in Slice 1 — "
            "calls return 501."
        ),
    ),
    include_edges: bool = Query(
        False, description="Include reasoning edges from session_edges (full only)"
    ),
    edge_limit: int = Query(
        20, ge=1, le=100, description="Max reasoning edges to return"
    ),
    include_compaction_pointers: bool = Query(
        False,
        description=(
            "§6.10: return the raw assertion stream including compaction-pointer "
            "rows. Default false — summaries surface first, pointers deprioritised. "
            "Applies to intent=full only."
        ),
    ),
    debug: bool = Query(
        False,
        description=(
            "§7.8 observability: when intent=card, attach a `debug` block "
            "exposing `fetch_plan_row_volume` so callers can verify card "
            "mode is executing a projection-aware fetch (§6.2), not a "
            "load-and-trim over the full payload."
        ),
    ),
    top_k: int = Query(
        _CARD_TOP_K_DEFAULT,
        ge=1,
        le=50,
        description=(
            "v2.4 §6.3: number of top-K active assertions in Card v0 payload. "
            "Tunable; default 7. Applies to intent=card only."
        ),
    ),
) -> dict[str, object]:
    """Fetch one entity at the requested intent.

    `intent=full` returns the legacy EntityDetail payload (with assertions,
    relationships, optional reasoning edges, compaction projection meta).
    `intent=card` returns the v2.4 Card v0 payload (§6.3): identity,
    status_summary, summary_row, top-K active assertions, edge_type_summary,
    section_manifest, archives_to_count, freshness, reserved
    `predicate_summary` slot.
    """
    source = request.headers.get("x-cortex-source", "agent")
    agent = request.headers.get("x-cortex-agent", "web")
    session_id = request.headers.get("x-cortex-session")
    if intent in _CARD_INTENTS_DEFERRED:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": f"intent={intent!r} reserved but not implemented in Slice 1",
                "supported_intents": ["full", "card"],
                "reference": "cortex-v2.4 §6.1, §7.1, §7.3",
            },
        )
    with cortex_conn() as conn:
        if intent == "card":
            return _get_entity_card_impl(
                conn,
                entity_id=entity_id,
                top_k=top_k,
                debug=debug,
                source=source,
                agent=agent,
                session_id=session_id,
            )
        return _get_entity_impl(
            conn,
            entity_id=entity_id,
            include_edges=include_edges,
            edge_limit=edge_limit,
            source=source,
            agent=agent,
            session_id=session_id,
            include_compaction_pointers=include_compaction_pointers,
        )


_JSON_COLUMNS = frozenset({"aliases", "attributes"})


def _emit_todo_closure_gap_if_needed(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
) -> None:
    """Emit a structured signal when a todo closes without an audit assertion.

    Per todo:cortex-todo-closure-payload AC5 — making the audit gap visible
    without blocking. Fires when ALL of:
      - entity is type=todo
      - workflow_state transitions TO 'done' (was != 'done', now == 'done')
      - the entity has zero assertions at the moment of transition

    Visibility, not enforcement. The agent or operator sees the signal via
    Event Service / cortex-api logs and can either re-close via the
    pipeline:todo-close path or backfill the audit trail manually.
    """
    if entity_type != "todo":
        return
    if new_workflow_state != "done":
        return
    if prior_workflow_state == "done":
        return
    rows = query(
        conn,
        "SELECT COUNT(*) AS n FROM assertions WHERE entity_id = ?",
        (entity_id,),
    )
    count = int(rows[0]["n"]) if rows else 0
    if count > 0:
        return
    logger.warning(
        "todo closure gap: %s transitioned to workflow_state=done with no "
        "assertions on the entity. Prefer pipeline:todo-close to capture "
        "summary + relationships + reasoning edges atomically.",
        entity_id,
    )
    record(
        "cortex.todo.closure.gap",
        entity_id=entity_id,
        prior_workflow_state=prior_workflow_state or "",
    )


def _update_entity_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    existing = query(
        conn,
        "SELECT id, type, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )

    prior_workflow_state = existing[0]["workflow_state"]

    if "workflow_state" in updates and updates["workflow_state"] is not None:
        _validate_workflow_state(
            conn, existing[0]["type"], str(updates["workflow_state"])
        )

    sets: list[str] = []
    params: list[object] = []
    for field, value in updates.items():
        if field in _JSON_COLUMNS:
            value = json_encode(value)
        sets.append(f"{field} = ?")
        params.append(value)

    if not sets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No updatable fields provided",
        )

    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sets.append("updated_at = ?")
    params.append(now)
    params.append(entity_id)
    execute(conn, f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", tuple(params))

    new_workflow_state = updates.get("workflow_state")
    if isinstance(new_workflow_state, str):
        _emit_todo_closure_gap_if_needed(
            conn,
            entity_id=entity_id,
            entity_type=str(existing[0]["type"]),
            new_workflow_state=new_workflow_state,
            prior_workflow_state=(
                str(prior_workflow_state) if prior_workflow_state is not None else None
            ),
        )

    rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    assertion_rows = query(
        conn,
        f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
        "ORDER BY created_at DESC",
        (entity_id,),
    )

    assertions: list[AssertionItem] = []
    for row in assertion_rows:
        try:
            assertions.append(AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )
    return EntityDetail(
        **decode_row(rows[0], _ENTITY_JSON_FIELDS), assertions=assertions
    ).model_dump(mode="json")


@router.patch("/{entity_id}", response_model=EntityDetail)
def update_entity(entity_id: str, body: EntityUpdate) -> EntityDetail:
    """Update mutable fields on an entity.

    Uses ``model_fields_set`` so omitted keys are untouched while explicitly
    sending ``null`` clears the field (sets it to SQL NULL).
    """
    updates = {field: getattr(body, field) for field in body.model_fields_set}
    with cortex_conn() as conn:
        result = _update_entity_impl(conn, entity_id=entity_id, updates=updates)
    return EntityDetail(**result)


def _create_entity_impl(
    conn: sqlite3.Connection, payload: dict[str, object]
) -> dict[str, object]:
    body = EntityCreate.model_validate(payload)
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    workflow_state = body.workflow_state
    if workflow_state is not None:
        _validate_workflow_state(conn, body.type, workflow_state)
    else:
        schema = _workflow_schema(conn, body.type)
        if schema is not None:
            workflow_state = str(schema["initial_state"])

    conn.execute(
        "INSERT INTO entities (id, type, name, description, status, "
        "workflow_state, aliases, "
        "attributes, notes, source_uri, content_hash, "
        "retention_policy, retention_ttl_days, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            body.id,
            body.type,
            body.name,
            body.description,
            body.status or "confirmed",
            workflow_state,
            json_encode(body.aliases),
            json_encode(body.attributes),
            body.notes,
            body.source_uri,
            body.content_hash,
            body.retention_policy or "permanent",
            body.retention_ttl_days,
            now,
            now,
        ),
    )
    conn.commit()
    rows = query(conn, "SELECT * FROM entities WHERE id = ?", (body.id,))
    if not rows:
        logger.error("Entity create succeeded but no row returned for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entity created but could not be read back",
        )
    return EntityDetail(
        **decode_row(rows[0], _ENTITY_JSON_FIELDS), assertions=[]
    ).model_dump(mode="json")


@router.post("", response_model=EntityDetail, status_code=status.HTTP_201_CREATED)
def create_entity(body: EntityCreate) -> EntityDetail:
    """Create an entity and return the stored entity detail payload.

    Failure modes are explicitly disambiguated so callers can react correctly:
      - 409 Conflict: duplicate ID (caller error, ¬retryable)
      - 503 Service Unavailable: transient sqlite degradation (retryable)
      - 500: unknown structural failure (fall-through)
    """
    with cortex_conn() as conn:
        try:
            result = _create_entity_impl(conn, body.model_dump())
        except sqlite3.IntegrityError:
            logger.warning("Entity create conflict for id=%s", body.id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "detail": f"Entity already exists: {body.id}",
                    "retryable": False,
                },
            )
        except sqlite3.OperationalError as exc:
            logger.error(
                "Entity create transient cortex degradation for id=%s: %s",
                body.id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": f"Cortex temporarily unavailable: {exc}",
                    "retryable": True,
                },
            )
    return EntityDetail(**result)
