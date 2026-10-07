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


VERDICT_MISSING = "live_verify.verdict_missing"
LIVE_DEFECT = "live_verify.live_defect"
LAND_SHA_NOT_LIVE = "live_verify.land_sha_not_live"
RULING_MISSING = "live_verify.ruling_missing"

_LIVE_VERIFY_KIND = "live_verify"
_SATISFIED_RELATIONS = frozenset({"equal", "ancestor"})
_LINE_VERDICTS = frozenset({"LIVE_OK", "LIVE_DEFECT", "LIVE_UNTESTABLE"})


def live_verify_required(attributes: object) -> bool:
    """True only when the todo opts into the close gate."""
    return (
        isinstance(attributes, dict) and attributes.get("live_verify_required") is True
    )


def _assertion_attributes(row: dict[str, object]) -> dict[str, object] | None:
    raw = row.get("attributes")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


def active_live_verify_rows(
    assertions: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    """Active verdict rows: kind=live_verify and superseded_by IS NULL."""
    rows: list[dict[str, object]] = []
    for row in assertions or []:
        if not isinstance(row, dict):
            continue
        if row.get("superseded_by") is not None:
            continue
        attrs = _assertion_attributes(row)
        if attrs is None or attrs.get("kind") != _LIVE_VERIFY_KIND:
            continue
        rows.append({**row, "attributes": attrs})
    return rows


def live_verify_close_refusal(
    attributes: object,
    assertions: list[dict[str, object]] | None,
) -> str | None:
    """Named refusal for a flagged todo, or None when close may proceed.

    Token order is the G4 bind. Unflagged todos return None. A flagged todo
    with no single readable active verdict row fails closed.
    """
    if not live_verify_required(attributes):
        return None
    rows = active_live_verify_rows(assertions)
    if len(rows) != 1:
        return VERDICT_MISSING
    attrs = rows[0]["attributes"]
    assert isinstance(attrs, dict)
    lines = attrs.get("lines")
    if not isinstance(lines, list) or not lines:
        return VERDICT_MISSING
    verdicts: list[object] = []
    for line in lines:
        if not isinstance(line, dict):
            return VERDICT_MISSING
        verdicts.append(line.get("verdict"))
    if any(verdict not in _LINE_VERDICTS for verdict in verdicts):
        return VERDICT_MISSING
    if any(verdict == "LIVE_DEFECT" for verdict in verdicts):
        return LIVE_DEFECT
    relations = attrs.get("service_relations")
    if not isinstance(relations, list) or not relations:
        return LAND_SHA_NOT_LIVE
    for rel in relations:
        if not isinstance(rel, dict):
            return LAND_SHA_NOT_LIVE
        if (
            rel.get("answer") != "yes"
            or rel.get("relation") not in _SATISFIED_RELATIONS
        ):
            return LAND_SHA_NOT_LIVE
    if any(verdict == "LIVE_UNTESTABLE" for verdict in verdicts):
        ruling = attrs.get("operator_ruling")
        if not isinstance(ruling, str) or not ruling.strip():
            return RULING_MISSING
    return None


def enforce_live_verify_on_done(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
    attributes: object,
) -> None:
    """Refuse a todo→done write when the live-verify gate fails.

    Shared by direct ``entity_update`` and any caller that commits
    ``workflow_state=done``. cortex-api does not open the manage socket;
    the sha check reads relations stored on the verdict row.
    """
    if entity_type != "todo":
        return
    if new_workflow_state != "done":
        return
    if prior_workflow_state == "done":
        return
    if not live_verify_required(attributes):
        return
    if not _table_exists(conn, "assertions"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=VERDICT_MISSING,
        )
    rows = query(
        conn,
        "SELECT id, entity_id, claim, attributes, superseded_by "
        "FROM assertions WHERE entity_id = ?",
        (entity_id,),
    )
    parsed: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        parsed.append(item)
    token = live_verify_close_refusal(attributes, parsed)
    if token is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=token,
        )


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


def emit_todo_done_side_effects(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
) -> None:
    """Post-commit hooks for todo → done (gap signal + spawned friction close)."""
    emit_todo_closure_gap_if_needed(
        conn,
        entity_id=entity_id,
        entity_type=entity_type,
        new_workflow_state=new_workflow_state,
        prior_workflow_state=prior_workflow_state,
    )
    # Local import: friction close pulls assertion write path; avoid cycle at
    # module load with entity_crud ↔ dispatch_ops.
    from .dispatch_ops._friction_followon_close import (
        close_spawned_friction_on_todo_done,
    )

    close_spawned_friction_on_todo_done(
        conn,
        entity_id=entity_id,
        entity_type=entity_type,
        new_workflow_state=new_workflow_state,
        prior_workflow_state=prior_workflow_state,
    )
