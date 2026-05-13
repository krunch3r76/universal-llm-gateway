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
from collections.abc import Callable

from fastapi import HTTPException, status

from .db import cortex_conn, decode_row, json_encode, query
from .entity_aliases import sync_entity_aliases
from .entity_exhibit_lint import (
    enforce_exhibit_belongs_to,
    insert_exhibit_belongs_to_relationship,
)
from .models import (
    AssertionItem,
    EntityCreate,
    EntityDetail,
    EntitySummary,
)
from .routes.assertions import _ASSERTION_COLS
from .type_schemas import validate_required_attributes
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


def update_entity_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    updates: dict[str, object],
    commit: bool = True,
    post_commit_emits: list[Callable[[], None]] | None = None,
) -> dict[str, object]:
    """Update an entity in place.

    Event emission for workflow_state transitions is deferred until after
    commit so that a rolled-back transaction does not leave a false signal
    on the bus. When ``commit=True`` (default), the emit fires inline after
    ``conn.commit()``. When ``commit=False``, the caller MUST pass
    ``post_commit_emits`` (a list to receive deferred callbacks); the caller
    is then responsible for invoking each callback AFTER its own commit.
    If ``commit=False`` and ``post_commit_emits`` is None, the workflow
    closure-gap signal is dropped silently rather than fired pre-commit.
    """
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
    conn.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", tuple(params))

    if "aliases" in updates:
        aliases = updates["aliases"]
        sync_entity_aliases(
            conn,
            entity_id=entity_id,
            entity_type=str(prior["type"]),
            aliases=aliases if isinstance(aliases, list) else None,
        )

    new_workflow_state = updates.get("workflow_state")
    closure_gap_emit: Callable[[], None] | None = None
    if isinstance(new_workflow_state, str):
        # Snapshot the predicate inputs while the transaction is still open.
        # The emit fires AFTER commit so a rolled-back transaction does not
        # leave a false cortex.todo.closure.gap signal on the bus.
        _entity_type_snap = str(prior["type"])
        _new_ws_snap = new_workflow_state
        _prior_ws_snap = (
            str(prior_workflow_state) if prior_workflow_state is not None else None
        )

        def _deferred_emit(
            _conn: sqlite3.Connection = conn,
            _eid: str = entity_id,
            _et: str = _entity_type_snap,
            _new: str = _new_ws_snap,
            _prior: str | None = _prior_ws_snap,
        ) -> None:
            emit_todo_closure_gap_if_needed(
                _conn,
                entity_id=_eid,
                entity_type=_et,
                new_workflow_state=_new,
                prior_workflow_state=_prior,
            )

        closure_gap_emit = _deferred_emit

    if commit:
        conn.commit()
        if closure_gap_emit is not None:
            closure_gap_emit()
    elif closure_gap_emit is not None and post_commit_emits is not None:
        post_commit_emits.append(closure_gap_emit)

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
    conn: sqlite3.Connection, payload: dict[str, object], commit: bool = True
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

    validate_required_attributes(conn, body.type, body.attributes)

    # Spec § 1.3 — exhibit entities require a `belongs_to (case:<slug>)`
    # relationship at write time. The hook validates the ID grammar and
    # the parent case's existence BEFORE the entity INSERT so a missing
    # case rejects the whole transaction.
    exhibit_parent_case_id = enforce_exhibit_belongs_to(
        conn,
        entity_id=body.id,
        entity_type=body.type,
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
    sync_entity_aliases(
        conn,
        entity_id=body.id,
        entity_type=body.type,
        aliases=body.aliases,
    )
    # Spec § 1.3 — auto-create the exhibit→case `belongs_to` row inside
    # the same transaction. enforce_exhibit_belongs_to above already
    # confirmed the parent case exists; this insert is the side-effect.
    if exhibit_parent_case_id is not None:
        insert_exhibit_belongs_to_relationship(
            conn,
            exhibit_id=body.id,
            case_id=exhibit_parent_case_id,
        )
    if commit:
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
