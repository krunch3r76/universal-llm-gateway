"""Tests for GET /skills — capability filtering; no applicable_agents field."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.skills import get_skills

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    source_uri TEXT,
    lifecycle TEXT,
    attributes TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert_skill(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    capabilities_required: list[str] | None = None,
    lifecycle: str | None = "active",
) -> None:
    attrs = (
        {"capabilities_required": capabilities_required}
        if capabilities_required is not None
        else None
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, description, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, 'Trigger text.', ?, ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            lifecycle,
            json.dumps(attrs) if attrs is not None else None,
        ),
    )


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


_ALL_ACTIVE = {
    "agent_skill:null-attr",
    "agent_skill:life-skill",
    "agent_skill:code-skill",
    "agent_skill:no-caps",
}


@pytest.fixture()
def skills_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert_skill(conn, "agent_skill:null-attr")
    _insert_skill(conn, "agent_skill:life-skill", capabilities_required=["mcp_life"])
    _insert_skill(conn, "agent_skill:code-skill", capabilities_required=["mcp_code"])
    _insert_skill(conn, "agent_skill:no-caps", capabilities_required=[])
    _insert_skill(
        conn,
        "agent_skill:deprecated-skill",
        capabilities_required=[],
        lifecycle="deprecated",
    )
    conn.commit()
    return conn


def test_null_attribute_visible_for_seat(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:null-attr" in _ids(body)


def test_life_skill_visible_on_life_seat(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:life-skill" in _ids(body)


def test_code_skill_hidden_on_life_seat(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:code-skill" not in _ids(body)


def test_code_skill_visible_on_code_seat(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-cursor")
    assert "agent_skill:code-skill" in _ids(body)
    assert "agent_skill:life-skill" in _ids(body)


def test_unknown_seat_slug_returns_422(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(limit=50, for_agent="bogus-seat")
    assert exc.value.status_code == 422


def test_legacy_web_alias_normalized(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="web")
    assert "agent_skill:life-skill" in _ids(body)
    assert "agent_skill:code-skill" not in _ids(body)


def test_no_for_agent_returns_all_non_deprecated(
    skills_conn: sqlite3.Connection,
) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent=None)
    ids = _ids(body)
    assert "agent_skill:null-attr" in ids
    assert "agent_skill:deprecated-skill" not in ids


def test_manifest_item_envelope_shape(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    item = next(i for i in body["items"] if i["id"] == "agent_skill:life-skill")
    assert set(item) >= {
        "id",
        "name",
        "trigger",
        "source_uri",
        "digest",
    }
    assert "applicable_agents" not in item
    assert body["count"] == len(body["items"])
