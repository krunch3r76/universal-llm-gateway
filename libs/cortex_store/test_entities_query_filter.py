"""Tests for `list_entities_impl` query-param substring filter."""

from __future__ import annotations

import sqlite3

from cortex_store.entity_crud import list_entities_impl


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
            aliases TEXT,
            attributes TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    return conn


def _insert(
    conn: sqlite3.Connection, eid: str, name: str, etype: str = "agent_skill"
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status, workflow_state, created_at) "
        "VALUES (?, ?, ?, 'confirmed', NULL, '2026-05-19T00:00:00Z')",
        (eid, etype, name),
    )


def test_query_filters_on_id_substring() -> None:
    conn = _conn()
    _insert(conn, "agent_skill:architecture-invariants", "Architecture Invariants")
    _insert(conn, "agent_skill:ulg-architecture", "ULG Architecture")
    _insert(conn, "agent_skill:other-skill", "Unrelated Skill")
    conn.commit()

    result = list_entities_impl(conn, query="architecture")
    ids = sorted(item["id"] for item in result["items"])
    assert ids == [
        "agent_skill:architecture-invariants",
        "agent_skill:ulg-architecture",
    ]


def test_query_is_case_insensitive_on_name() -> None:
    conn = _conn()
    _insert(conn, "person:kaywan-mansubi", "Kaywan Mansubi", etype="person")
    _insert(conn, "person:other", "Other Person", etype="person")
    conn.commit()

    result = list_entities_impl(conn, query="MANSUBI")
    assert [item["id"] for item in result["items"]] == ["person:kaywan-mansubi"]


def test_query_composes_with_type_filter() -> None:
    conn = _conn()
    _insert(conn, "agent_skill:architecture-invariants", "Architecture Invariants")
    _insert(conn, "todo:architecture-cleanup", "Architecture cleanup", etype="todo")
    conn.commit()

    result = list_entities_impl(conn, entity_type="agent_skill", query="architecture")
    assert [item["id"] for item in result["items"]] == [
        "agent_skill:architecture-invariants"
    ]


def test_empty_query_is_treated_as_absent() -> None:
    conn = _conn()
    _insert(conn, "agent_skill:a", "A")
    _insert(conn, "agent_skill:b", "B")
    conn.commit()

    # Empty string and whitespace-only must NOT apply a `%%` LIKE that would
    # short-circuit other filters or match every row by coincidence.
    result_empty = list_entities_impl(conn, query="")
    result_ws = list_entities_impl(conn, query="   ")
    assert len(result_empty["items"]) == 2
    assert len(result_ws["items"]) == 2


def test_query_absent_returns_all() -> None:
    conn = _conn()
    _insert(conn, "agent_skill:a", "A")
    _insert(conn, "agent_skill:b", "B")
    conn.commit()

    result = list_entities_impl(conn)
    assert len(result["items"]) == 2


def test_query_composes_with_workflow_state() -> None:
    """`query` AND `workflow_state` both apply — Finding 2 (sibling SQL clauses)."""
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, status, workflow_state, created_at) "
        "VALUES ('todo:arch-cleanup', 'todo', 'Arch cleanup', 'confirmed', 'open', '2026-05-19T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status, workflow_state, created_at) "
        "VALUES ('todo:arch-done', 'todo', 'Arch done', 'confirmed', 'done', '2026-05-19T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status, workflow_state, created_at) "
        "VALUES ('todo:other-open', 'todo', 'Other open', 'confirmed', 'open', '2026-05-19T00:00:00Z')"
    )
    conn.commit()

    result = list_entities_impl(conn, workflow_state="open", query="arch")
    assert [item["id"] for item in result["items"]] == ["todo:arch-cleanup"]


def test_query_composes_with_for_agent() -> None:
    """`query` AND `for_agent` both apply — Finding 2 (json_each subquery shape)."""
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, status, attributes, created_at) "
        "VALUES ('agent_skill:web-arch', 'agent_skill', 'Web Arch', 'confirmed', "
        "'{\"applicable_agents\": [\"web\"]}', '2026-05-19T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status, attributes, created_at) "
        "VALUES ('agent_skill:cursor-arch', 'agent_skill', 'Cursor Arch', 'confirmed', "
        "'{\"applicable_agents\": [\"cursor\"]}', '2026-05-19T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status, attributes, created_at) "
        "VALUES ('agent_skill:web-other', 'agent_skill', 'Web Other', 'confirmed', "
        "'{\"applicable_agents\": [\"web\"]}', '2026-05-19T00:00:00Z')"
    )
    conn.commit()

    result = list_entities_impl(conn, for_agent="web", query="arch")
    assert [item["id"] for item in result["items"]] == ["agent_skill:web-arch"]


def test_query_treats_percent_as_literal() -> None:
    """`%` in user input must NOT act as a LIKE wildcard — Finding 1 regression guard."""
    conn = _conn()
    _insert(conn, "promo:50%-off", "50% off promotion")
    _insert(conn, "promo:other", "Other promo")
    conn.commit()

    # Literal `%` should match only the row containing a literal `%`.
    result = list_entities_impl(conn, query="50%")
    assert [item["id"] for item in result["items"]] == ["promo:50%-off"]

    # `%` alone must not return everything (would be a `%%%` LIKE pattern bug).
    result_just_percent = list_entities_impl(conn, query="%")
    assert [item["id"] for item in result_just_percent["items"]] == ["promo:50%-off"]


def test_query_treats_underscore_as_literal() -> None:
    """`_` in user input must NOT act as a LIKE wildcard — Finding 1 regression guard."""
    conn = _conn()
    _insert(conn, "case:uber_2026", "Uber 2026 case")
    _insert(conn, "case:uberX2026", "UberX 2026 case")
    conn.commit()

    # Literal `_` should match only the row containing a literal `_`.
    result = list_entities_impl(conn, query="uber_")
    assert [item["id"] for item in result["items"]] == ["case:uber_2026"]


def test_query_escapes_backslash_in_input() -> None:
    """A backslash in user input must not corrupt the ESCAPE '\\' contract."""
    conn = _conn()
    _insert(conn, "path:c\\users", "Windows-style path")
    _insert(conn, "path:c-users", "Linux-style path")
    conn.commit()

    result = list_entities_impl(conn, query="c\\u")
    assert [item["id"] for item in result["items"]] == ["path:c\\users"]
