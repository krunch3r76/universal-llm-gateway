from __future__ import annotations

import datetime
import json
import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.db import decode_row, execute, query, todos_conn
from src.models import TodoCreate, TodoItem, TodoList, TodoStatusUpdate

logger = logging.getLogger("cortex-api.todos")
router = APIRouter(prefix="/todos", tags=["todos"])

_JSON_FIELDS = frozenset({"refs"})


@router.get("", response_model=TodoList)
def list_todos(
    status_filter: str = Query("open", alias="status"),
    context: str | None = None,
    domain: str | None = None,
    priority: str | None = None,
    limit: int = Query(30, ge=1, le=200),
) -> TodoList:
    """List todos filtered by status/context/domain/priority with bounded result size."""
    clauses: list[str] = []
    params: list[str | int] = []

    if status_filter != "all":
        clauses.append("status = ?")
        params.append(status_filter)
    if domain:
        clauses.append("domain LIKE ?")
        params.append(f"%{domain}%")
    if context:
        clauses.append("context = ?")
        params.append(context)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM todos{where} ORDER BY rowid LIMIT ?"
    params.append(limit)

    conn = todos_conn()
    try:
        rows = query(conn, sql, tuple(params))
    finally:
        conn.close()

    return TodoList(items=[TodoItem(**decode_row(row, _JSON_FIELDS)) for row in rows])


@router.post("", response_model=TodoItem, status_code=status.HTTP_201_CREATED)
def create_todo(body: TodoCreate) -> TodoItem:
    """Create a todo item and return the stored record after insert."""
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    refs_json = json.dumps(body.refs) if body.refs else "{}"

    conn = todos_conn()
    try:
        conn.execute(
            "INSERT INTO todos (id, title, domain, context, priority, "
            "description, refs, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                body.id,
                body.title,
                body.domain,
                body.context,
                body.priority,
                body.description,
                refs_json,
                now,
                now,
            ),
        )
        conn.commit()
        rows = query(conn, "SELECT * FROM todos WHERE id = ?", (body.id,))
    except conn.IntegrityError:
        logger.warning("Todo create conflict for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Todo already exists: {body.id}",
        )
    finally:
        conn.close()

    if not rows:
        logger.error("Todo create succeeded but no row returned for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Todo created but could not be read back",
        )
    return TodoItem(**decode_row(rows[0], _JSON_FIELDS))


@router.patch("/{todo_id}", response_model=TodoItem)
def update_todo_status(todo_id: str, body: TodoStatusUpdate) -> TodoItem:
    """Update todo status and return the updated row."""
    allowed = {"done", "deferred", "open"}
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {body.status!r}. Must be one of {sorted(allowed)}",
        )

    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = todos_conn()
    try:
        affected = execute(
            conn,
            "UPDATE todos SET status = ?, updated_at = ? WHERE id = ?",
            (body.status, now, todo_id),
        )
        if affected == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Todo not found: {todo_id}",
            )
        rows = query(conn, "SELECT * FROM todos WHERE id = ?", (todo_id,))
    finally:
        conn.close()

    if not rows:
        logger.error("Todo update committed but no row returned for id=%s", todo_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Todo updated but could not be read back",
        )
    return TodoItem(**decode_row(rows[0], _JSON_FIELDS))
