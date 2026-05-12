"""Entity CRUD impls extracted from routes/entities.py.

Holds the ``_*_impl`` functions for entity persistence. The HTTP route
handlers in ``routes/entities.py`` are thin wrappers that call into this
module; the dispatch ops in ``dispatch_ops/ops_entities.py`` import the
impls directly.

Workflow-state schema, validation, and todo-closure-gap emission live in
``workflow_state.py`` (split per SLOC waiver assertion 8521 on
``spec:cortex-v2.4``).
"""

from __future__ import annotations

import datetime
import logging
import sqlite3

from fastapi import HTTPException, status

from .action_hints import detect_expired_unresolved
from .compaction import apply_compaction_filter
from .db import cortex_conn, decode_row, execute, json_encode, query
from .models import (
    AssertionItem,
    CompactionProjection,
    EdgeItem,
    EntityCreate,
    EntityDetail,
    EntitySummary,
    RelationshipItem,
)
from .routes.assertions import _ASSERTION_COLS
from .routes.edges import _EDGE_COLS
from .workflow_state import (
    emit_todo_closure_gap_if_needed,
    validate_workflow_state,
    workflow_schema,
)

logger = logging.getLogger("cortex-api.entity_crud")

ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})


def _enforce_role_entity_lint(
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    description: str | None,
    attributes: object,
) -> None:
    """Reject role entities whose free-text fields fail self-concept lint (R1–R3)."""
    if entity_type != "role":
        return
    from role_lint import RoleLintError, lint_role_payload

    attrs = attributes if isinstance(attributes, dict) else {}
    payload: dict[str, object] = {
        "id": entity_id,
        "type": entity_type,
        "name": name,
        "description": description or "",
        "attributes": attrs,
    }
    try:
        lint_role_payload(payload)
    except RoleLintError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "role_lint_rejected",
                "message": str(exc),
                "violations": [
                    {
                        "field_path": v.field_path,
                        "rule_class": v.rule_class,
                        "matched_fragment": v.matched_fragment,
                    }
                    for v in exc.violations
                ],
            },
        ) from exc


ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})
JSON_COLUMNS = frozenset({"aliases", "attributes"})

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


def list_entities_impl(
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
        # slugs that should see the entity. NULL/missing → universal via
        # COALESCE so pre-backfill behaviour is "include".
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


def get_entity_impl(
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
            assertions.append(AssertionItem(**decode_row(row, ASSERTION_JSON_FIELDS)))
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
        **decode_row(entity, ENTITY_JSON_FIELDS),
        assertions=assertions,
        relationships=relationships,
        reasoning_edges=edges,
        action_hints=hints or None,
        compaction_projection=compaction_projection,
    ).model_dump(mode="json")


def update_entity_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    full_rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not full_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )
    prior = decode_row(full_rows[0], ENTITY_JSON_FIELDS)
    prior_workflow_state = prior.get("workflow_state")

    merged: dict[str, object] = dict(prior)
    for field, value in updates.items():
        if value is None:
            continue
        if field == "attributes" and isinstance(value, dict):
            base_attrs = dict(merged.get("attributes") or {})
            base_attrs.update(value)
            merged["attributes"] = base_attrs
        else:
            merged[field] = value

    if str(prior.get("type")) == "role":
        _enforce_role_entity_lint(
            entity_id=str(merged["id"]),
            entity_type=str(merged["type"]),
            name=str(merged.get("name") or ""),
            description=(
                str(merged["description"])
                if merged.get("description") is not None
                else None
            ),
            attributes=merged.get("attributes"),
        )

    if "workflow_state" in updates and updates["workflow_state"] is not None:
        validate_workflow_state(
            conn, str(prior["type"]), str(updates["workflow_state"])
        )

    sets: list[str] = []
    params: list[object] = []
    for field, value in updates.items():
        if field in JSON_COLUMNS:
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
        emit_todo_closure_gap_if_needed(
            conn,
            entity_id=entity_id,
            entity_type=str(prior["type"]),
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
            assertions.append(AssertionItem(**decode_row(row, ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )
    return EntityDetail(
        **decode_row(rows[0], ENTITY_JSON_FIELDS), assertions=assertions
    ).model_dump(mode="json")


def create_entity_impl(
    conn: sqlite3.Connection, payload: dict[str, object]
) -> dict[str, object]:
    body = EntityCreate.model_validate(payload)
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if body.type == "role":
        _enforce_role_entity_lint(
            entity_id=body.id,
            entity_type=body.type,
            name=body.name,
            description=body.description,
            attributes=dict(body.attributes or {}),
        )

    workflow_state = body.workflow_state
    if workflow_state is not None:
        validate_workflow_state(conn, body.type, workflow_state)
    else:
        schema = workflow_schema(conn, body.type)
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
        **decode_row(rows[0], ENTITY_JSON_FIELDS), assertions=[]
    ).model_dump(mode="json")


# Module-level convenience for callers that want a connection-scoped variant.
def list_entities_with_conn(**kwargs: object) -> dict[str, list[dict[str, object]]]:
    with cortex_conn() as conn:
        return list_entities_impl(conn, **kwargs)  # type: ignore[arg-type]
