"""Stage-A graph-backed related_skills boost (Slice B / thread 2011 P4)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.routes._skill_index import boot_skill_row
from cortex_store.routes._skill_suggest import run_stage_a
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


def _insert(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    source_uri: str,
    trigger_match_terms: list[str],
    related_skills: list[str] | None = None,
    applicable_agents: list[str] | None = None,
) -> None:
    attrs: dict[str, object] = {
        "applicable_agents": applicable_agents or ["claude-web"],
        "trigger_match_terms": trigger_match_terms,
        "delivery_priority": 100,
    }
    if related_skills is not None:
        attrs["related_skills"] = related_skills
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, 'active', ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            json.dumps(attrs),
        ),
    )


def _run(
    conn: sqlite3.Connection,
    context: str,
    *,
    loaded: list[str] | None = None,
    limit: int = 8,
) -> dict:
    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        return run_stage_a(
            agent="claude-web",
            loaded=loaded or [],
            conversation_context=context,
            limit=limit,
        )


def _related_boost_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
        trigger_match_terms=["dispatch", "handoff"],
        related_skills=["friction-review"],
    )
    _insert(
        conn,
        "agent_skill:friction-review",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/friction-review/SKILL.md",
        trigger_match_terms=["friction", "defect", "triage"],
    )
    _insert(
        conn,
        "agent_skill:debug-with-events",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/debug-with-events/SKILL.md",
        trigger_match_terms=["debug", "events", "pipeline"],
    )
    conn.commit()
    return conn


@pytest.mark.offline
def test_get_boot_skills_returns_related_skills() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
        trigger_match_terms=["dispatch"],
        related_skills=["friction-review", "architecture-invariants"],
    )
    conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        payload = get_skills(for_agent="claude-web", limit=10, view="boot")
    item = next(i for i in payload["items"] if i["id"] == "agent_skill:consult-routing")
    assert item["related_skills"] == [
        "friction-review",
        "architecture-invariants",
    ]


@pytest.mark.offline
def test_boot_skill_row_emits_related_skills_array() -> None:
    row = {
        "id": "agent_skill:consult-routing",
        "name": "consult-routing",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
        "skill_binding_json": None,
        "trigger_short": None,
        "skill_category": None,
        "trigger_match_terms_json": json.dumps(["dispatch"]),
        "boot_importance": None,
        "related_skills_json": json.dumps(
            ["friction-review", "architecture-invariants"]
        ),
    }
    item = boot_skill_row(row)
    assert item["related_skills"] == [
        "friction-review",
        "architecture-invariants",
    ]


@pytest.mark.offline
def test_boot_skill_row_related_skills_defaults_empty() -> None:
    row = {
        "id": "agent_skill:foo",
        "name": "foo",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/foo/SKILL.md",
        "skill_binding_json": None,
        "trigger_short": None,
        "skill_category": None,
        "trigger_match_terms_json": None,
        "boot_importance": None,
        "related_skills_json": None,
    }
    assert boot_skill_row(row)["related_skills"] == []


@pytest.mark.offline
def test_stage_a_related_boost_ranks_companion_when_parent_loaded() -> None:
    context = "friction triage on the event pipeline debug path"
    without = _run(_related_boost_conn(), context)
    with_parent = _run(_related_boost_conn(), context, loaded=["consult-routing"])

    without_slugs = [s["slug"] for s in without["suggestions"]]
    with_slugs = [s["slug"] for s in with_parent["suggestions"]]
    assert "friction-review" in with_slugs
    assert "debug-with-events" in with_slugs
    assert with_slugs.index("friction-review") < with_slugs.index("debug-with-events")
    assert without_slugs.index("debug-with-events") <= without_slugs.index(
        "friction-review"
    )


@pytest.mark.offline
def test_stage_a_related_boost_is_deterministic() -> None:
    context = "friction triage on the event pipeline debug path"
    loaded = ["consult-routing"]
    first = _run(_related_boost_conn(), context, loaded=loaded)
    second = _run(_related_boost_conn(), context, loaded=loaded)
    assert first["suggestions"] == second["suggestions"]


@pytest.mark.offline
def test_stage_a_related_boost_one_hop_only() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:parent-a",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/parent-a/SKILL.md",
        trigger_match_terms=["parent"],
        related_skills=["middle-b"],
    )
    _insert(
        conn,
        "agent_skill:middle-b",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/middle-b/SKILL.md",
        trigger_match_terms=["middle"],
        related_skills=["leaf-c"],
    )
    _insert(
        conn,
        "agent_skill:leaf-c",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/leaf-c/SKILL.md",
        trigger_match_terms=["leaf", "companion"],
    )
    conn.commit()
    result = _run(conn, "middle workflow parent", loaded=["parent-a"])
    slugs = {s["slug"] for s in result["suggestions"]}
    assert "middle-b" in slugs
    assert "leaf-c" not in slugs


@pytest.mark.offline
def test_stage_a_related_boost_does_not_bypass_precision_gate() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
        trigger_match_terms=["dispatch"],
        related_skills=["advisor-timing"],
    )
    _insert(
        conn,
        "agent_skill:advisor-timing",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/advisor-timing/SKILL.md",
        trigger_match_terms=["consult", "decision"],
    )
    conn.commit()
    result = _run(conn, "consult", loaded=["consult-routing"])
    assert "advisor-timing" not in {s["slug"] for s in result["suggestions"]}
