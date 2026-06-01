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
import json
import re
import sqlite3
from collections.abc import Callable

from fastapi import HTTPException, status
from universal_logging import get_logger

from .db import cortex_conn, decode_row, json_encode
from .db import query as db_query
from .entity_aliases import sync_entity_aliases
from .entity_exhibit_lint import (
    enforce_exhibit_belongs_to,
    insert_exhibit_belongs_to_relationship,
)
from .event_publisher import cortex_entity_source_changed
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

logger = get_logger("cortex-api.entity_crud")

# Mirror of _COHERENCE_RULES keys in dispatch_ops/_detectors/workflow_coherence.py.
# A workflow-typed entity whose status='confirmed' carries adopted/canonical
# semantics is born PROVISIONAL so its birth state (provisional+initial_state)
# is coherent; adoption advances both axes together via entity_update.
# ∀ type ∈ _PROVISIONAL_BIRTH_TYPES: birth default = "provisional" (not "confirmed").
# Widen this set only after a dependency check scoped to the candidate type.
_PROVISIONAL_BIRTH_TYPES = frozenset({"decision"})

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

# Base columns selectable directly; everything else resolves from the
# attributes JSON blob via json_extract. `id` is always projected.
_PROJECTABLE_COLUMNS = frozenset(
    {
        "id",
        "type",
        "name",
        "description",
        "status",
        "workflow_state",
        "content_hash",
        "created_at",
    }
)
_SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _build_field_projection(fields: list[str]) -> tuple[str, list[str]]:
    """Return (SELECT-list SQL, ordered output keys) for a projected query.

    `id` is always included. Base columns select directly; non-column names
    resolve as json_extract(attributes,'$.<name>') AS <name>. Field names are
    validated against a simple identifier grammar to keep them out of SQL
    string interpolation risk (no params for identifiers/JSON paths in sqlite).
    """
    out_keys: list[str] = ["id"]
    select_parts: list[str] = ["id"]
    for raw in fields:
        name = raw.strip()
        if not name or name == "id" or not _SAFE_FIELD.fullmatch(name):
            continue
        out_keys.append(name)
        if name in _PROJECTABLE_COLUMNS:
            select_parts.append(name)
        else:
            select_parts.append(f"json_extract(attributes, '$.{name}') AS {name}")
    return ", ".join(select_parts), out_keys


def _project_row(row: dict[str, object], out_keys: list[str]) -> dict[str, object]:
    """Keep only the projected keys; decode JSON-list attribute values.

    json_extract returns a JSON-encoded string for list/object values; decode
    those so e.g. applicable_agents comes back as a Python list, not a string.
    """
    out: dict[str, object] = {}
    for k in out_keys:
        v = row.get(k)
        if isinstance(v, str) and v[:1] in ("[", "{"):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


def list_entities_impl(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    workflow_state: str | None = None,
    limit: int = 50,
    for_agent: str | None = None,
    query: str | None = None,
    content_hash: str | None = None,
    fields: list[str] | None = None,
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

    if content_hash is not None:
        # Strip sha256: prefix — the column stores raw hex. Callers may
        # pass either form; normalise here so both work.
        normalized_hash = content_hash.removeprefix("sha256:")
        if normalized_hash:
            clauses.append("content_hash = ?")
            params.append(normalized_hash)

    # Case-insensitive LITERAL substring filter on id and name. SQLite
    # LIKE is ASCII case-insensitive by default (no PRAGMA
    # case_sensitive_like changes assumed). `%` and `_` in user input are
    # escaped so they match literally rather than acting as LIKE
    # wildcards — without escaping, `query="%"` returns all rows and
    # `query="abc_def"` would match `abcXdef`. Empty/whitespace-only
    # query is treated as absent.
    if query is not None:
        stripped = query.strip()
        if stripped:
            escaped = (
                stripped.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            clauses.append("(id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
            params.append(pattern)
            params.append(pattern)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if fields:
        select_sql, out_keys = _build_field_projection(fields)
        sql = (
            f"SELECT {select_sql} FROM entities{where} ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)
        rows = db_query(conn, sql, tuple(params))
        return {"items": [_project_row(row, out_keys) for row in rows]}

    sql = (
        "SELECT id, type, name, description, status, workflow_state, content_hash, "
        f"created_at FROM entities{where} ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)
    rows = db_query(conn, sql, tuple(params))
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
    full_rows = db_query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
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

    source_uri_emit: Callable[[], None] | None = None
    if "source_uri" in updates:
        _eid_su = entity_id
        _new_su = updates.get("source_uri")

        def _deferred_source_emit(
            _eid: str = _eid_su,
            _new: object = _new_su,
        ) -> None:
            cortex_entity_source_changed(
                entity_id=_eid,
                change="dropped" if not _new else "changed",
                source_uri=_new if isinstance(_new, str) else None,
            )

        source_uri_emit = _deferred_source_emit

    if commit:
        conn.commit()
        if closure_gap_emit is not None:
            closure_gap_emit()
        if source_uri_emit is not None:
            source_uri_emit()
    else:
        if closure_gap_emit is not None and post_commit_emits is not None:
            post_commit_emits.append(closure_gap_emit)
        if source_uri_emit is not None and post_commit_emits is not None:
            post_commit_emits.append(source_uri_emit)

    rows = db_query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    assertion_rows = db_query(
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

    default_status = (
        "provisional" if body.type in _PROVISIONAL_BIRTH_TYPES else "confirmed"
    )
    status = body.status or default_status

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
            status,
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
        if body.source_uri:
            # Refresh nudge for the RAG EntityAdmissionGate; backstop self-heals
            # if this races a deferred commit (commit=False callers are covered
            # by the periodic backstop). Never blocks the write path.
            cortex_entity_source_changed(
                entity_id=body.id, change="set", source_uri=body.source_uri
            )
    rows = db_query(conn, "SELECT * FROM entities WHERE id = ?", (body.id,))
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
