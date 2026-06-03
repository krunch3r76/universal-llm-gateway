"""Tests for `list_entities_impl` field projection (`fields=[...]`)."""

from __future__ import annotations

import sqlite3

from cortex_store.entity_crud import list_entities_impl
from cortex_store.models import EntitySummary


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
            lifecycle TEXT,
            confidence_band TEXT,
            adoption TEXT,
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


def _insert_skill(
    conn: sqlite3.Connection,
    eid: str,
    *,
    confidence_band: str = "confirmed",
    applicable_agents: list[str] | None = None,
) -> None:
    attrs = (
        f'{{"applicable_agents": {__import__("json").dumps(applicable_agents)}}}'
        if applicable_agents is not None
        else None
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, confidence_band, attributes, created_at) "
        "VALUES (?, 'agent_skill', ?, ?, ?, '2026-05-19T00:00:00Z')",
        (eid, eid.split(":", 1)[-1], confidence_band, attrs),
    )


def test_fields_projection_decodes_attribute_list() -> None:
    conn = _conn()
    _insert_skill(
        conn,
        "agent_skill:audit-a",
        applicable_agents=["web", "cursor"],
    )
    _insert_skill(
        conn,
        "agent_skill:audit-b",
        applicable_agents=["grok-direct"],
    )
    conn.commit()

    result = list_entities_impl(
        conn,
        entity_type="agent_skill",
        fields=["status", "applicable_agents"],
    )
    assert len(result["items"]) == 2
    for item in result["items"]:
        assert set(item.keys()) == {"id", "status", "applicable_agents"}
        assert item["status"] == "confirmed"
        assert isinstance(item["applicable_agents"], list)


def test_fields_absent_returns_entity_summary_shape() -> None:
    conn = _conn()
    _insert_skill(conn, "agent_skill:legacy")
    conn.commit()

    result = list_entities_impl(conn, entity_type="agent_skill")
    assert len(result["items"]) == 1
    item = result["items"][0]
    EntitySummary(**item)
    assert "type" in item
    assert "name" in item
    assert "applicable_agents" not in item


def test_unsafe_field_names_are_dropped_not_interpolated() -> None:
    conn = _conn()
    _insert_skill(conn, "agent_skill:safe")
    conn.commit()

    malicious = "'; DROP TABLE entities; --"
    result = list_entities_impl(conn, fields=["status", malicious])
    assert len(result["items"]) == 1
    assert malicious not in result["items"][0]
    assert set(result["items"][0].keys()) == {"id", "status"}
    # Table survived — injection was not interpolated into SQL.
    still_there = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert still_there == 1
