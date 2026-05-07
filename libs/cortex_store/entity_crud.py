"""Entity CRUD impls + workflow-state helpers extracted from routes/entities.py.

Holds the ``_*_impl`` functions and workflow-schema validation. The HTTP
route handlers in ``routes/entities.py`` are thin wrappers that call into
this module; the dispatch ops in ``dispatch_ops/ops_entities.py`` import
the impls directly.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3

from fastapi import HTTPException, status

from .action_hints import detect_expired_unresolved
from .compaction import apply_compaction_filter
from .db import cortex_conn, decode_row, execute, json_encode, query
from .dispatch_ops._shared import record
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

logger = logging.getLogger("cortex-api.entity_crud")

ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})
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


def workflow_schema(
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


def validate_workflow_state(
    conn: sqlite3.Connection, entity_type: str, value: str
) -> None:
    """Reject *value* if entity_type has a registered enum that excludes it.

    Types without a registered schema accept any value (free-form).
    """
    schema = workflow_schema(conn, entity_type)
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


def _emit_todo_closure_gap_if_needed(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
) -> None:
    """Emit a structured signal when a todo closes without an audit assertion.

    Per todo:cortex-todo-closure-payload AC5 — visibility, not enforcement.
    Fires when a todo transitions to workflow_state='done' with zero assertions
    on the entity.
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


def update_entity_impl(
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
        validate_workflow_state(
            conn, existing[0]["type"], str(updates["workflow_state"])
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
