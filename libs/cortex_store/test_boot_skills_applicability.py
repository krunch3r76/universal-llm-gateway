"""Tests for GET /boot-skills seat applicability (default-deny + slug validation)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.boot.skills import get_boot_skills

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
    lifecycle: str | None = None,
) -> None:
    attrs: dict[str, object] | None = None
    if applicable_agents is not None:
        attrs = {"applicable_agents": applicable_agents}
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


@pytest.fixture()
def skills_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert_skill(conn, "agent_skill:null-attr")  # Test A — attribute absent
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


def test_null_attribute_withheld_from_seat(skills_conn: sqlite3.Connection) -> None:
    """Test A: NULL-attribute skill excluded when for_agent is set (default-deny)."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:null-attr" not in _ids(body)


def test_cursor_only_withheld_from_web(skills_conn: sqlite3.Connection) -> None:
    """Test B: explicit cursor-only skill excluded from claude-web."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:cursor-only" not in _ids(body)


def test_universal_included_for_any_seat(skills_conn: sqlite3.Connection) -> None:
    """Test C: explicit ['*'] skill included."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:universal" in _ids(body)


def test_self_seat_included(skills_conn: sqlite3.Connection) -> None:
    """Test D: skill listing the requested seat is included."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:web-only" in _ids(body)


def test_multi_seat_excludes_unlisted_seat(skills_conn: sqlite3.Connection) -> None:
    """Test E: multi-seat skill excluded from a seat not in its list."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="gpt-cursor")
    assert "agent_skill:cursor-web" not in _ids(body)


def test_unknown_seat_slug_returns_422(skills_conn: sqlite3.Connection) -> None:
    """Test F2-validate: unknown for_agent slug raises HTTP 422."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        with pytest.raises(HTTPException) as exc:
            get_boot_skills(limit=50, for_agent="bogus-seat")
    assert exc.value.status_code == 422


def test_legacy_web_alias_normalized_and_matched(
    skills_conn: sqlite3.Connection,
) -> None:
    """Test F2-legacy: legacy 'web' normalizes to claude-web and matches."""
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="web")
    assert "agent_skill:web-only" in _ids(body)


def test_deprecated_skills_excluded(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:deprecated-skill" not in _ids(body)
