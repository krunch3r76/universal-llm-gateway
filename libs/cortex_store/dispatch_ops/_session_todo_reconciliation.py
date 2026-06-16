"""Session-close todo reconciliation — surface open todos in entity_ids."""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn, query

_OPEN_TODO_STATES = frozenset({"open", "in_progress"})


def open_todos_in_entity_ids(entity_ids: list[str] | None) -> list[dict[str, str]]:
    """Return todo entities in *entity_ids* still open or in_progress."""
    todo_ids = [eid for eid in entity_ids or [] if eid.startswith("todo:")]
    if not todo_ids:
        return []

    placeholders = ",".join("?" * len(todo_ids))
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, workflow_state FROM entities "
            f"WHERE id IN ({placeholders}) AND type = 'todo'",
            tuple(todo_ids),
        )

    pending: list[dict[str, str]] = []
    for row in rows:
        state = str(row.get("workflow_state") or "")
        if state in _OPEN_TODO_STATES:
            pending.append({"todo_id": row["id"], "workflow_state": state})
    return pending


def todo_reconciliation_warning(pending: list[dict[str, str]]) -> str | None:
    if not pending:
        return None
    slugs = ", ".join(item["todo_id"] for item in pending)
    return (
        f"todo_reconciliation.required: {slugs} still "
        f"open/in_progress in entity_ids — run entity_get, determine completion, "
        "then pipeline(todo-close) before session_close (session-close.mdc §0c)."
    )


def todo_reconciliation_preflight_fields(
    entity_ids: list[str] | None,
) -> dict[str, Any]:
    pending = open_todos_in_entity_ids(entity_ids)
    out: dict[str, Any] = {"open_todos_in_entity_ids": pending}
    warning = todo_reconciliation_warning(pending)
    if warning:
        out["todo_reconciliation_warning"] = warning
    return out


__all__ = [
    "open_todos_in_entity_ids",
    "todo_reconciliation_preflight_fields",
    "todo_reconciliation_warning",
]
