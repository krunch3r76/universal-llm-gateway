"""Tests for GET /skills?view=boot — all skills visible; applicable_agents is metadata."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.skills import get_skills
from cortex_store.seat_applicability import validate_applicable_agents

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
    applicable_agents: list[str] | None = None,
    lifecycle: str | None = "active",
    include_empty_attrs: bool = False,
) -> None:
    attrs: dict[str, object] | None = None
    if applicable_agents is not None:
        attrs = {"applicable_agents": applicable_agents}
    elif include_empty_attrs:
        attrs = {}
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


def _boot(for_agent: str, conn: sqlite3.Connection, *, limit: int = 50) -> dict:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        return get_skills(limit=limit, for_agent=for_agent, view="boot")


_ALL_ACTIVE = {
    "agent_skill:null-attr",
    "agent_skill:empty-attr",
    "agent_skill:cursor-only",
    "agent_skill:universal",
    "agent_skill:web-only",
    "agent_skill:cursor-web",
}


@pytest.fixture()
def skills_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert_skill(conn, "agent_skill:null-attr")
    _insert_skill(conn, "agent_skill:empty-attr", applicable_agents=[])
    _insert_skill(conn, "agent_skill:cursor-only", applicable_agents=["claude-cursor"])
    _insert_skill(conn, "agent_skill:universal", applicable_agents=["*"])
    _insert_skill(conn, "agent_skill:web-only", applicable_agents=["claude-web"])
    _insert_skill(
        conn,
        "agent_skill:cursor-web",
        applicable_agents=["claude-cursor", "claude-web"],
    )
    _insert_skill(
        conn,
        "agent_skill:deprecated-skill",
        applicable_agents=["*"],
        lifecycle="deprecated",
    )
    conn.commit()
    return conn


@pytest.mark.parametrize(
    "seat",
    ["claude-cursor", "claude-web", "claude-api", "grok-api-multi", "gpt-cursor"],
)
def test_all_active_skills_visible_for_every_seat(
    skills_conn: sqlite3.Connection,
    seat: str,
) -> None:
    body = _boot(seat, skills_conn)
    assert _ids(body) == _ALL_ACTIVE


@pytest.mark.parametrize("seat", ["claude-cursor", "claude-web", "claude-api"])
def test_null_attribute_visible_for_all_seats(
    skills_conn: sqlite3.Connection,
    seat: str,
) -> None:
    body = _boot(seat, skills_conn)
    assert "agent_skill:null-attr" in _ids(body)


@pytest.mark.parametrize("seat", ["claude-cursor", "claude-web", "grok-api-multi"])
def test_empty_applicable_agents_visible_for_all_seats(
    skills_conn: sqlite3.Connection,
    seat: str,
) -> None:
    body = _boot(seat, skills_conn)
    assert "agent_skill:empty-attr" in _ids(body)


def test_lead_scoped_skill_visible_on_api_platform(
    skills_conn: sqlite3.Connection,
) -> None:
    body = _boot("claude-api", skills_conn)
    assert "agent_skill:cursor-web" in _ids(body)


def test_applicable_agents_projected_as_metadata(skills_conn: sqlite3.Connection) -> None:
    body = _boot("claude-web", skills_conn)
    item = next(i for i in body["items"] if i["id"] == "agent_skill:cursor-web")
    assert item["applicable_agents"] == ["claude-cursor", "claude-web"]


def test_unknown_seat_slug_returns_422(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(limit=50, for_agent="bogus-seat", view="boot")
    assert exc.value.status_code == 422


def test_legacy_web_alias_normalized(skills_conn: sqlite3.Connection) -> None:
    body = _boot("web", skills_conn)
    assert _ids(body) == _ALL_ACTIVE


def test_deprecated_skills_excluded(skills_conn: sqlite3.Connection) -> None:
    body = _boot("claude-web", skills_conn)
    assert "agent_skill:deprecated-skill" not in _ids(body)


def test_write_unknown_applicable_agents_slug_accepted() -> None:
    validate_applicable_agents({"applicable_agents": ["web-anthropic"]})
