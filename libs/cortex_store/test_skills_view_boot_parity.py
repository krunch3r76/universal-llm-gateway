"""Golden parity: GET /skills?view=boot boot projection (F2b)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.boot._skill_trigger import skill_description_text
from cortex_store.routes.skills import get_skills
from cortex_store.skill_listing_format import (
    render_concise_skill_index,
    render_skills_card_section,
)
from cortex_store.tests.boot_card_golden import (
    assert_card_matches_legacy_golden,
    legacy_card_golden_bytes,
)

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

_RANKING_FIELDS = (
    "boot_importance",
    "trigger_match_terms",
    "related_skills",
    "skill_class",
    "binding_kind",
    "tool_binding",
)


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
    attributes_extra: dict[str, object] | None = None,
    source_uri: str | None = None,
) -> None:
    attrs: dict[str, object] = {}
    if applicable_agents is not None:
        attrs["applicable_agents"] = applicable_agents
    if attributes_extra:
        attrs.update(attributes_extra)
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, 'Trigger text.', ?, ?, ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            lifecycle,
            json.dumps(attrs) if attrs else None,
        ),
    )


def _parity_fixture_conn() -> sqlite3.Connection:
    conn = _conn()
    _insert_skill(conn, "agent_skill:null-attr")
    _insert_skill(
        conn,
        "agent_skill:alpha",
        applicable_agents=["claude-web"],
        attributes_extra={
            "trigger_short": "alpha trigger",
            "skill_category": "planning",
            "trigger_match_terms": ["alpha", "plan"],
            "boot_importance": "required_gate",
            "related_skills": ["agent_skill:beta"],
            "skill_binding": {
                "skill_class": "tool_manual",
                "tool_binding": {"exposure": "primary", "tool": "cortex"},
            },
        },
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/alpha/SKILL.md",
    )
    _insert_skill(
        conn,
        "agent_skill:beta",
        applicable_agents=["*"],
        attributes_extra={
            "trigger_short": "beta trigger",
            "skill_category": "planning",
            "related_skills": ["gamma"],
        },
    )
    _insert_skill(
        conn,
        "agent_skill:cursor-only",
        applicable_agents=["claude-cursor"],
        attributes_extra={"skill_category": "misc"},
    )
    conn.commit()
    return conn


def _ranking_slice(item: dict) -> dict:
    return {k: item.get(k) for k in _RANKING_FIELDS if k in item}


@pytest.fixture()
def parity_conn() -> sqlite3.Connection:
    return _parity_fixture_conn()


def test_boot_view_envelope_shape(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(limit=50, layer="skills", for_agent="claude-web", view="boot")
    assert set(body) == {"items", "layer"}
    assert body["layer"] == "skills"
    assert len(body["items"]) == 4


def test_boot_view_ranking_fields_present(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(limit=50, layer="skills", for_agent="claude-web", view="boot")
    alpha = next(i for i in body["items"] if i["id"] == "agent_skill:alpha")
    assert _ranking_slice(alpha) == {
        "boot_importance": "required_gate",
        "trigger_match_terms": ["alpha", "plan"],
        "related_skills": ["beta"],
        "skill_class": "tool_manual",
        "binding_kind": "mcp_primary",
        "tool_binding": {"exposure": "primary", "tool": "cortex"},
    }


def test_boot_view_rules_layer_envelope(parity_conn: sqlite3.Connection) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, lifecycle, attributes) "
        "VALUES ('rule:sample', 'rule', 'sample', 'Rule trigger.', 'active', ?)",
        (json.dumps({"applicable_agents": ["claude-web"]}),),
    )
    conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        body = get_skills(limit=50, layer="rules", for_agent="claude-web", view="boot")
    assert set(body) == {"items", "layer"}
    assert body["layer"] == "rules"
    assert [i["id"] for i in body["items"]] == ["rule:sample"]


def test_default_skills_shape_unchanged(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    assert set(body) == {"items", "for_agent", "layer", "count"}
    assert body["for_agent"] == "claude-web"
    assert body["layer"] == "skills"
    assert body["count"] == len(body["items"])
    item = next(i for i in body["items"] if i["id"] == "agent_skill:beta")
    assert "trigger" in item
    assert item["name"] == "beta"
    assert "schema_version" not in body
    assert "view" not in body
    assert "rendered" not in body


def test_render_concise_one_load_contract_line() -> None:
    rows = [
        {
            "id": "agent_skill:alpha",
            "name": "Alpha",
            "skill_category": "planning",
            "description_first_sentence": "Alpha summary",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/alpha/SKILL.md",
        },
        {
            "id": "agent_skill:beta",
            "name": "Beta",
            "skill_category": "planning",
            "description_first_sentence": "Beta summary",
            "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/beta/SKILL.md",
        },
    ]
    md = render_concise_skill_index(rows)
    assert md.count("**Use a body**") == 1
    assert "cortex:agent-skills/" not in md
    assert "**Source**:" not in md
    assert "### alpha" in md
    assert "### beta" in md


def test_render_concise_via_skills_endpoint() -> None:
    manifest_conn = _parity_fixture_conn()
    boot_conn = _parity_fixture_conn()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=manifest_conn):
        manifest = get_skills(
            limit=50,
            for_agent="claude-web",
            render="concise",
        )
    with patch("cortex_store.routes.skills.cortex_conn", return_value=boot_conn):
        boot = get_skills(
            limit=50,
            for_agent="claude-web",
            view="boot",
            render="concise",
        )
    assert "rendered" in manifest
    assert "rendered" in boot
    manifest_md = manifest["rendered"]["concise_markdown"]
    boot_md = boot["rendered"]["concise_markdown"]
    assert manifest_md.count("**Use a body**") == 1
    assert boot_md.count("**Use a body**") == 1
    assert "cortex:agent-skills/" not in manifest_md
    assert "cortex:agent-skills/" not in boot_md


def test_unknown_view_returns_422(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(view="bogus")
    assert exc.value.status_code == 422


def test_summary_resolvers_converge_for_trigger_short(
    parity_conn: sqlite3.Connection,
) -> None:
    """Manifest trigger, skill_description_text, and boot-card line agree for trigger_short."""
    expected = "alpha trigger"
    boot_conn = _parity_fixture_conn()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        manifest = get_skills(limit=50, for_agent="claude-web")
    with patch("cortex_store.routes.skills.cortex_conn", return_value=boot_conn):
        boot = get_skills(
            limit=50,
            layer="skills",
            for_agent="claude-web",
            view="boot",
        )
    alpha_manifest = next(i for i in manifest["items"] if i["id"] == "agent_skill:alpha")
    alpha_boot = next(i for i in boot["items"] if i["id"] == "agent_skill:alpha")
    manifest_trigger = alpha_manifest["trigger"]
    description_text = skill_description_text(dict(alpha_boot))
    card_md = render_skills_card_section(boot["items"])
    alpha_line = next(line for line in card_md.splitlines() if "`alpha`" in line)
    card_trigger = alpha_line.split("`alpha`", 1)[1].split(" [", 1)[0].removeprefix(" — ")
    assert manifest_trigger == expected
    assert description_text == expected
    assert card_trigger == expected
    assert manifest_trigger == description_text == card_trigger


def test_render_card_section_legacy_golden_parity(parity_conn: sqlite3.Connection) -> None:
    """render_skills_card_section bytes match git-historical render_skills_section golden."""
    from cortex_store.skill_listing_format import render_skills_card_section

    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(
            limit=50,
            layer="skills",
            for_agent="claude-web",
            view="boot",
        )
    rendered = render_skills_card_section(body["items"])
    assert rendered == legacy_card_golden_bytes()


def test_render_card_section_endpoint_roundtrip(parity_conn: sqlite3.Connection) -> None:
    """Card markdown from GET /skills?view=boot&render=card matches legacy golden."""
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(
            limit=50,
            layer="skills",
            for_agent="claude-web",
            view="boot",
            render="card",
        )
    assert_card_matches_legacy_golden(body["rendered"]["card_markdown"])


def test_render_card_via_skills_endpoint(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(
            limit=50,
            layer="skills",
            for_agent="claude-web",
            view="boot",
            render="card",
        )
    assert_card_matches_legacy_golden(body["rendered"]["card_markdown"])


def test_render_card_with_manifest_view_returns_422(
    parity_conn: sqlite3.Connection,
) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(limit=50, for_agent="claude-web", render="card")
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "invalid_render_for_view"


def test_render_concise_and_card_combined(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(
            limit=50,
            for_agent="claude-web",
            view="boot",
            render="concise,card",
        )
    assert "concise_markdown" in body["rendered"]
    assert "card_markdown" in body["rendered"]


def test_unknown_render_returns_422(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(render="bogus")
    assert exc.value.status_code == 422


def test_render_concise_accepted(parity_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=parity_conn):
        body = get_skills(limit=50, for_agent="claude-web", render="concise")
    assert "concise_markdown" in body["rendered"]
