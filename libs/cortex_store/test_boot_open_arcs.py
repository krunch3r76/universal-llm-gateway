"""Tests for GET /boot-recent-work open_arcs payload."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from cortex_store.routes.boot.recent_work import get_boot_recent_work

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT,
    name TEXT,
    workflow_state TEXT,
    description TEXT,
    attributes TEXT,
    updated_at TEXT
);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity TEXT,
    to_entity TEXT,
    type TEXT,
    active INTEGER DEFAULT 1,
    valid_until TEXT
);
CREATE TABLE relationship_types (type TEXT PRIMARY KEY, description TEXT);
INSERT INTO relationship_types (type, description) VALUES ('child_of', '');
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def test_open_arcs_returns_child_todos_for_task() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('task:arc', 'task', 'Arc', 'in_progress', '2026-06-08')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('todo:a', 'todo', 'A', 'open', '2026-06-08')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('todo:b', 'todo', 'B', 'open', '2026-06-08')"
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, valid_until) "
        "VALUES ('todo:a', 'task:arc', 'child_of', 1, NULL)"
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, valid_until) "
        "VALUES ('todo:b', 'task:arc', 'child_of', 1, NULL)"
    )
    conn.commit()

    with patch("cortex_store.routes.boot.recent_work.cortex_conn", return_value=conn):
        body = get_boot_recent_work(phase_limit=3, todo_limit=5, arc_limit=5)

    assert "plan_phases" in body
    assert "in_flight_todos" in body
    assert len(body["open_arcs"]) == 1
    child_ids = {c["id"] for c in body["open_arcs"][0]["children"]}
    assert child_ids == {"todo:a", "todo:b"}


def test_open_arcs_excludes_soft_deleted_child_edge() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('task:arc', 'task', 'Arc', 'open', '2026-06-08')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('todo:gone', 'todo', 'Gone', 'open', '2026-06-08')"
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, valid_until) "
        "VALUES ('todo:gone', 'task:arc', 'child_of', 1, '2026-06-09')"
    )
    conn.commit()

    with patch("cortex_store.routes.boot.recent_work.cortex_conn", return_value=conn):
        body = get_boot_recent_work(phase_limit=3, todo_limit=5, arc_limit=5)

    assert body["open_arcs"][0]["children"] == []


def test_open_arcs_excludes_done_tasks() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, updated_at) "
        "VALUES ('task:done', 'task', 'Done', 'done', '2026-06-08')"
    )
    conn.commit()

    with patch("cortex_store.routes.boot.recent_work.cortex_conn", return_value=conn):
        body = get_boot_recent_work(phase_limit=3, todo_limit=5, arc_limit=5)

    assert body["open_arcs"] == []
