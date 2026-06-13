"""Regression: partial attributes update merges into the prior blob.

Exposed by todo_close_sidecar (todo:todo-closure-summary-sidecar): setting
``attributes.closure_summary_uri`` on a todo that already carried
``priority``/``domain`` previously clobbered the whole attributes column,
because ``update_entity_impl`` computed a merged dict but the SQL write
iterated the raw ``updates``. This pins the merge semantics.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._todo_closure_sidecar import render_closure_markdown
from cortex_store.entity_crud import update_entity_impl


def _seed_todo(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name, attributes, created_at) "
        "VALUES ('todo:x', 'todo', 'X', ?, '2026-06-03T00:00:00Z')",
        (json.dumps({"priority": "medium", "domain": "cortex"}),),
    )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_todo(migrated_conn)
    return migrated_conn


def test_partial_attributes_update_preserves_prior_keys(
    conn: sqlite3.Connection,
) -> None:
    update_entity_impl(
        conn,
        entity_id="todo:x",
        updates={"attributes": {"closure_summary_uri": "cortex://notes/x-closure.md"}},
    )
    row = conn.execute("SELECT attributes FROM entities WHERE id='todo:x'").fetchone()
    attrs = json.loads(row["attributes"])
    assert attrs == {
        "priority": "medium",
        "domain": "cortex",
        "closure_summary_uri": "cortex://notes/x-closure.md",
    }


def test_attributes_update_overwrites_same_key(conn: sqlite3.Connection) -> None:
    update_entity_impl(
        conn, entity_id="todo:x", updates={"attributes": {"priority": "high"}}
    )
    row = conn.execute("SELECT attributes FROM entities WHERE id='todo:x'").fetchone()
    attrs = json.loads(row["attributes"])
    assert attrs == {"priority": "high", "domain": "cortex"}


def test_render_closure_markdown_shape() -> None:
    md = render_closure_markdown(
        todo_id="todo:x",
        summary="Did the thing.",
        evidence="Closure of todo:x.",
        reasoning_summary="Because.",
        references=[{"target": "todo:y", "role": "extends"}],
        agent="cursor",
        session_id="cursor-2026-06-03-001000-a01",
        closed_at="2026-06-03T00:10:00Z",
    )
    assert md.startswith("# Closure — todo:x")
    assert "## Summary" in md
    assert "Did the thing." in md
    assert "## Reasoning" in md
    assert "## References" in md
    assert "`todo:y` (extends)" in md
    assert md.endswith("\n")
