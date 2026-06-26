"""Workflow-state schema, validation, and closure-gap emission.

Split from entity_crud.py (SLOC waiver assertion 8521 on spec:cortex-v2.4)
to keep entity CRUD focused on persistence and to give the workflow-state
contract its own home: schema lookup, enum validation, and the
todo-closure-gap signal (visibility, not enforcement).
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import HTTPException, status
from universal_logging import get_logger

from .db import query
from .dispatch_ops._shared import record

logger = get_logger("cortex-api.workflow_state")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = query(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return bool(rows)


def workflow_schema(
    conn: sqlite3.Connection, entity_type: str
) -> dict[str, object] | None:
    """Fetch the workflow schema for *entity_type* if registered, else None.

    Returns None when the ``workflow_schemas`` registry table is absent —
    mirrors the graceful-degradation pattern in ``type_schemas`` so test
    fixtures and pre-migration databases stay usable.
    """
    if not _table_exists(conn, "workflow_schemas"):
        return None
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


def closure_audit_exempt(conn: sqlite3.Connection, entity_type: str) -> bool:
    """Return True if *entity_type* is registered as closure-audit-exempt.

    Exempt types (e.g. ``condition``) must never appear in open-debt or
    closure audits. The flag is seeded by migration 060 via the
    ``closure_audit_exempt`` column on ``workflow_schemas``. Absent rows,
    absent tables, and types without a schema all return False — free-form
    types retain prior (non-exempt) behaviour.
    """
    if not _table_exists(conn, "workflow_schemas"):
        return False
    has_col = any(
        row[1] == "closure_audit_exempt"
        for row in conn.execute("PRAGMA table_info(workflow_schemas)").fetchall()
    )
    if not has_col:
        return False
    rows = query(
        conn,
        "SELECT closure_audit_exempt FROM workflow_schemas WHERE entity_type = ?",
        (entity_type,),
    )
    if not rows:
        return False
    return bool(rows[0]["closure_audit_exempt"])


def emit_todo_closure_gap_if_needed(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
) -> None:
    """Emit a structured signal when a todo closes without an audit assertion.

    Per todo:cortex-todo-closure-payload AC5 — visibility, not enforcement.
    Fires when a todo transitions to workflow_state='done' with zero
    assertions on the entity.
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
