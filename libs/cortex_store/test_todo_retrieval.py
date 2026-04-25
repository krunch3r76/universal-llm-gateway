from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cortex_store.routes.todo_audit import get_todo_audit
from cortex_store.routes.todo_retrieval import _query_todo_candidates


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            workflow_state TEXT,
            attributes TEXT,
            source_uri TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            superseded_by INTEGER
        );
        CREATE VIRTUAL TABLE assertions_fts USING fts5(
            assertion_id UNINDEXED,
            entity_id UNINDEXED,
            indexed_text
        );
        CREATE TABLE session_edges (
            id INTEGER PRIMARY KEY,
            from_node TEXT,
            to_node TEXT,
            valid_until TEXT
        );
        """
    )
    return conn


def _insert_todo(
    conn: sqlite3.Connection,
    todo_id: str,
    name: str,
    *,
    description: str,
    priority: str,
    domain: str,
    updated_days_ago: int = 1,
    source_uri: str | None = None,
) -> None:
    now = datetime.now(UTC)
    updated_at = (now - timedelta(days=updated_days_ago)).isoformat()
    attrs = json.dumps({"priority": priority, "domain": domain})
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, description, status, workflow_state, attributes,
            source_uri, created_at, updated_at
        )
        VALUES (?, 'todo', ?, ?, 'confirmed', 'open', ?, ?, ?, ?)
        """,
        (todo_id, name, description, attrs, source_uri, now.isoformat(), updated_at),
    )


def test_todo_candidates_rank_user_intent_without_open_enumeration() -> None:
    conn = _conn()
    _insert_todo(
        conn,
        "todo:cortex-todo-retrieval-revision",
        "Revise Cortex TODO retrieval",
        description="Reduce noisy open TODO listing with ranked retrieval.",
        priority="high",
        domain="cortex",
        source_uri="tasks/specs/cortex-todo-retrieval-revision.md",
    )
    _insert_todo(
        conn,
        "todo:unrelated",
        "Review unrelated infrastructure",
        description="Infrastructure cleanup.",
        priority="high",
        domain="infra",
    )
    conn.execute(
        "INSERT INTO session_edges (from_node, to_node, valid_until) VALUES (?, ?, NULL)",
        ("todo:cortex-todo-retrieval-revision", "transcript:test"),
    )
    conn.commit()

    result = _query_todo_candidates(
        conn,
        q="execute on the todo regarding cortex retrieval",
        limit=5,
    )

    assert result["retrieval"] == "ranked_intent"
    assert result["items"][0]["id"] == "todo:cortex-todo-retrieval-revision"
    assert [item["id"] for item in result["items"]] == [
        "todo:cortex-todo-retrieval-revision"
    ]


def test_todo_audit_identifies_stale_unlinked_missing_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _insert_todo(
        conn,
        "todo:old",
        "Old open TODO",
        description="Needs review.",
        priority="medium",
        domain="cortex",
        updated_days_ago=120,
    )

    monkeypatch.setattr("cortex_store.routes.todo_audit.cortex_conn", lambda: conn)
    result = get_todo_audit(stale_days=60, limit=10, domain=None, priority=None)

    assert result["items"][0]["id"] == "todo:old"
    assert result["items"][0]["audit_reasons"] == [
        "stale_open",
        "missing_spec",
        "unlinked",
    ]
    assert result["items"][0]["recommendation"] == "defer_close_or_convert_to_spec"
