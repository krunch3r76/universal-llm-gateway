"""Lifecycle filter regression tests for run_stage_a — discoverable = active only."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.confidence_field import SUPPRESSED_SKILL_LIFECYCLES
from cortex_store.routes._skill_suggest import run_stage_a

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
    lifecycle: str | None,
    trigger_match_terms: list[str],
    related_skills: list[str] | None = None,
) -> None:
    attrs: dict[str, object] = {
        "applicable_agents": ["claude-web"],
        "trigger_match_terms": trigger_match_terms,
    }
    if related_skills is not None:
        attrs["related_skills"] = related_skills
    conn.execute(
        "INSERT INTO entities (id, type, name, lifecycle, source_uri, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            lifecycle,
            "workspaces://universal-llm-gateway/.cursor/skills/placeholder/SKILL.md",
            json.dumps(attrs),
        ),
    )


@pytest.mark.offline
@pytest.mark.parametrize("suppressed_lifecycle", SUPPRESSED_SKILL_LIFECYCLES)
def test_suppressed_lifecycle_not_in_suggestions(suppressed_lifecycle: str) -> None:
    conn = _conn()
    _insert_skill(
        conn,
        f"agent_skill:suppressed-{suppressed_lifecycle}",
        lifecycle=suppressed_lifecycle,
        trigger_match_terms=["probe", "lifecycle", "test"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="probe lifecycle test",
            limit=8,
        )

    assert result["suggestions"] == []
    assert result["degraded"] is False


@pytest.mark.offline
def test_active_lifecycle_surfaces_normally() -> None:
    conn = _conn()
    _insert_skill(
        conn,
        "agent_skill:active-skill",
        lifecycle="active",
        trigger_match_terms=["active", "lifecycle", "skill"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="active lifecycle skill",
            limit=8,
        )

    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["id"] == "agent_skill:active-skill"


@pytest.mark.offline
def test_null_lifecycle_withheld_from_discovery() -> None:
    """NULL-detector regression: unset lifecycle withheld from skill_suggest (todo:audit-agent-skill-lifecycle-backfill F2/F4)."""
    conn = _conn()
    _insert_skill(
        conn,
        "agent_skill:null-lifecycle-skill",
        lifecycle=None,
        trigger_match_terms=["null", "lifecycle", "skill"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="null lifecycle skill",
            limit=8,
        )

    assert result["suggestions"] == []
    assert result["degraded"] is False


@pytest.mark.offline
def test_mixed_suppressed_and_active_only_active_surfaces() -> None:
    conn = _conn()
    _insert_skill(
        conn,
        "agent_skill:visible",
        lifecycle="active",
        trigger_match_terms=["mixed", "skill"],
    )
    for lc in ("draft", "retired", "merged"):
        _insert_skill(
            conn,
            f"agent_skill:hidden-{lc}",
            lifecycle=lc,
            trigger_match_terms=["mixed", "skill"],
        )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="mixed skill probe",
            limit=8,
        )

    ids = {s["id"] for s in result["suggestions"]}
    assert ids == {"agent_skill:visible"}


@pytest.mark.offline
def test_inactive_related_skill_not_injected_when_parent_loaded() -> None:
    conn = _conn()
    _insert_skill(
        conn,
        "agent_skill:parent-loaded",
        lifecycle="active",
        trigger_match_terms=["parent", "loaded"],
        related_skills=["inactive-child"],
    )
    _insert_skill(
        conn,
        "agent_skill:inactive-child",
        lifecycle="draft",
        trigger_match_terms=["parent", "loaded", "child"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=["parent-loaded"],
            conversation_context="parent loaded child probe",
            limit=8,
        )

    assert "agent_skill:inactive-child" not in {s["id"] for s in result["suggestions"]}
