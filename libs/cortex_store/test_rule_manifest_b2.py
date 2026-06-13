"""B2 A–F + Slice G tests — capability filter, layer discovery, /skills/body."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.entity_crud import (
    create_entity_impl,
    list_entities_impl,
    update_entity_impl,
)
from cortex_store.models import EntityCreate
from cortex_store.routes._skill_index import body_digest
from cortex_store.routes.boot.skills import get_boot_skills
from cortex_store.routes.skills import get_skill_body, get_skills

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    source_uri TEXT,
    lifecycle TEXT,
    attributes TEXT,
    workflow_state TEXT,
    aliases TEXT,
    notes TEXT,
    content_hash TEXT,
    retention_policy TEXT,
    retention_ttl_days INTEGER,
    created_at TEXT,
    updated_at TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert_rule(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    applicable_agents: list[str] | None = None,
    capabilities_required: list[str] | None = None,
    source_uri: str | None = None,
    delivery_priority: int | None = None,
) -> None:
    attrs: dict[str, object] = {}
    if applicable_agents is not None:
        attrs["applicable_agents"] = applicable_agents
    if capabilities_required is not None:
        attrs["capabilities_required"] = capabilities_required
    if delivery_priority is not None:
        attrs["delivery_priority"] = delivery_priority
    now = "2026-06-11T00:00:00Z"
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, attributes, created_at, updated_at) "
        "VALUES (?, 'rule', ?, 'Rule trigger.', ?, ?, ?, ?)",
        (
            entity_id,
            entity_id.removeprefix("rule:"),
            source_uri,
            json.dumps(attrs) if attrs else None,
            now,
            now,
        ),
    )


def _insert_skill(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    applicable_agents: list[str] | None = None,
    capabilities_required: list[str] | None = None,
    lifecycle: str | None = None,
    source_uri: str | None = None,
) -> None:
    attrs: dict[str, object] = {}
    if applicable_agents is not None:
        attrs["applicable_agents"] = applicable_agents
    if capabilities_required is not None:
        attrs["capabilities_required"] = capabilities_required
    now = "2026-06-11T00:00:00Z"
    conn.execute(
        "INSERT INTO entities (id, type, name, description, source_uri, lifecycle, attributes, created_at, updated_at) "
        "VALUES (?, 'agent_skill', ?, 'Trigger text.', ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            lifecycle,
            json.dumps(attrs) if attrs else None,
            now,
            now,
        ),
    )


def _insert_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    entity_type: str,
    *,
    applicable_agents: list[str] | None = None,
) -> None:
    attrs = {"applicable_agents": applicable_agents} if applicable_agents else None
    now = "2026-06-11T00:00:00Z"
    conn.execute(
        "INSERT INTO entities (id, type, name, description, attributes, created_at, updated_at) "
        "VALUES (?, ?, ?, 'desc', ?, ?, ?)",
        (
            entity_id,
            entity_type,
            entity_id.split(":", 1)[-1],
            json.dumps(attrs) if attrs else None,
            now,
            now,
        ),
    )


@pytest.fixture()
def skills_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = _conn()
    skill_file = tmp_path / "sample.md"
    skill_file.write_text("# Sample\nBody text.", encoding="utf-8")
    _insert_skill(
        conn,
        "agent_skill:index-envelope",
        applicable_agents=["claude-web"],
        source_uri=str(skill_file),
    )
    _insert_skill(
        conn,
        "agent_skill:mcp-only",
        applicable_agents=["*"],
        capabilities_required=["mcp_fs"],
    )
    _insert_skill(conn, "agent_skill:no-cap", applicable_agents=["claude-web"])
    _insert_skill(conn, "agent_skill:null-attr")
    _insert_skill(conn, "agent_skill:cursor-only", applicable_agents=["claude-cursor"])
    _insert_skill(conn, "agent_skill:web-only", applicable_agents=["claude-web"])
    conn.commit()
    return conn


def test_t1_boot_skills_index_envelope(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.boot.skills.cortex_conn", return_value=skills_conn):
        body = get_boot_skills(limit=50, for_agent="claude-web")
    item = next(i for i in body["items"] if i["id"] == "agent_skill:index-envelope")
    assert item.get("source_uri")
    assert item.get("digest", "").startswith("sha256:")


def test_t2_capability_all_of_excludes_inline_only() -> None:
    def _seed() -> sqlite3.Connection:
        conn = _conn()
        _insert_skill(
            conn,
            "agent_skill:mcp-only",
            applicable_agents=["*"],
            capabilities_required=["mcp_fs"],
        )
        conn.commit()
        return conn

    with patch("cortex_store.routes.skills.cortex_conn", return_value=_seed()):
        inline = get_skills(limit=50, for_agent="grok-api-multi")
    with patch("cortex_store.routes.skills.cortex_conn", return_value=_seed()):
        mcp = get_skills(limit=50, for_agent="gpt-cursor")
    inline_ids = {i["id"] for i in inline["items"]}
    mcp_ids = {i["id"] for i in mcp["items"]}
    assert "agent_skill:mcp-only" not in inline_ids
    assert "agent_skill:mcp-only" in mcp_ids


def test_t3_capability_absent_included(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    assert "agent_skill:no-cap" in {i["id"] for i in body["items"]}


def test_t4_write_guard_unknown_slug_create(skills_conn: sqlite3.Connection) -> None:
    payload = EntityCreate(
        id="agent_skill:bad-slug-test",
        type="agent_skill",
        name="bad-slug-test",
        attributes={"applicable_agents": ["bogus-seat"]},
    ).model_dump()
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(skills_conn, payload, commit=False)
    assert exc.value.status_code == 422


def test_t5_write_guard_universal_accepted(skills_conn: sqlite3.Connection) -> None:
    payload = EntityCreate(
        id="agent_skill:universal-test",
        type="agent_skill",
        name="universal-test",
        attributes={"applicable_agents": ["*"]},
    ).model_dump()
    out = create_entity_impl(skills_conn, payload, commit=False)
    assert out["id"] == "agent_skill:universal-test"


def test_t6_write_guard_unknown_slug_update(skills_conn: sqlite3.Connection) -> None:
    _insert_skill(
        skills_conn, "agent_skill:update-me", applicable_agents=["claude-web"]
    )
    skills_conn.commit()
    with pytest.raises(HTTPException) as exc:
        update_entity_impl(
            skills_conn,
            entity_id="agent_skill:update-me",
            updates={"attributes": {"applicable_agents": ["bogus-seat"]}},
            commit=False,
        )
    assert exc.value.status_code == 422


def test_t7_capability_token_enum_rejected(skills_conn: sqlite3.Connection) -> None:
    payload = EntityCreate(
        id="agent_skill:warp-drive",
        type="agent_skill",
        name="warp-drive",
        attributes={"capabilities_required": ["warp_drive"]},
    ).model_dump()
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(skills_conn, payload, commit=False)
    assert exc.value.status_code == 422


def test_t8_entities_deny_flip_null_excluded(skills_conn: sqlite3.Connection) -> None:
    _insert_skill(skills_conn, "agent_skill:null-entity", applicable_agents=None)
    skills_conn.commit()
    out = list_entities_impl(
        skills_conn, entity_type="agent_skill", for_agent="claude-web", limit=50
    )
    ids = {item["id"] for item in out["items"]}
    assert "agent_skill:null-entity" not in ids


def test_t9_entities_explicit_cursor_excluded_for_web(
    skills_conn: sqlite3.Connection,
) -> None:
    out = list_entities_impl(
        skills_conn, entity_type="agent_skill", for_agent="claude-web", limit=50
    )
    ids = {item["id"] for item in out["items"]}
    assert "agent_skill:cursor-only" not in ids


def test_t10_entities_legacy_web_normalized(skills_conn: sqlite3.Connection) -> None:
    out = list_entities_impl(
        skills_conn, entity_type="agent_skill", for_agent="web", limit=50
    )
    ids = {item["id"] for item in out["items"]}
    assert "agent_skill:web-only" in ids


def test_t11_skills_body_digest_drift_409(
    skills_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    skill_file = tmp_path / "drift.md"
    skill_file.write_text("stable body", encoding="utf-8")
    _insert_skill(
        skills_conn,
        "agent_skill:drift-test",
        source_uri=str(skill_file),
    )
    skills_conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        with pytest.raises(HTTPException) as exc:
            get_skill_body(
                id="agent_skill:drift-test", expected_digest="sha256:deadbeef"
            )
    assert exc.value.status_code == 409


def test_t12_skills_body_returns_substantive_sot(
    skills_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    sot = tmp_path / "universal-llm-gateway" / "docs" / "agent-guides" / "skills"
    sot.mkdir(parents=True)
    sot_file = sot / "architecture-invariants.md"
    sot_file.write_text(
        "# Architecture Invariants\nSubstantive SOT body.", encoding="utf-8"
    )
    _insert_skill(
        skills_conn,
        "agent_skill:architecture-invariants",
        source_uri="workspaces://universal-llm-gateway/docs/agent-guides/skills/architecture-invariants.md",
    )
    skills_conn.commit()
    with (
        patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn),
        patch(
            "cortex_store.routes.boot._skill_trigger._WORKSPACES_ROOT",
            tmp_path,
        ),
    ):
        out = get_skill_body(
            id="agent_skill:architecture-invariants", expected_digest=None
        )
    assert "Substantive SOT body" in out["body"]
    assert "SOT:" not in out["body"] or "discovery index only" not in out["body"]


def test_tg2_rule_cursor_only_excluded_for_web(skills_conn: sqlite3.Connection) -> None:
    _insert_rule(
        skills_conn,
        "rule:cursor-conduct",
        applicable_agents=["claude-cursor"],
    )
    skills_conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, layer="rules", for_agent="claude-web")
    assert "rule:cursor-conduct" not in {i["id"] for i in body["items"]}


def test_tg3_layer_all_returns_skill_and_rule(skills_conn: sqlite3.Connection) -> None:
    _insert_rule(
        skills_conn,
        "rule:universal-conduct",
        applicable_agents=["*"],
    )
    skills_conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, layer="all", for_agent="claude-web")
    ids = {i["id"] for i in body["items"]}
    assert any(i.startswith("agent_skill:") for i in ids)
    assert "rule:universal-conduct" in ids


def test_tg4_layer_skills_default_unchanged(skills_conn: sqlite3.Connection) -> None:
    _insert_rule(
        skills_conn,
        "rule:universal-conduct",
        applicable_agents=["*"],
    )
    skills_conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, for_agent="claude-web")
    ids = {i["id"] for i in body["items"]}
    assert all(i.startswith("agent_skill:") for i in ids)
    assert "rule:universal-conduct" not in ids


def test_tg5_unknown_layer_returns_422(skills_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        with pytest.raises(HTTPException) as exc:
            get_skills(limit=50, layer="bogus", for_agent="claude-web")
    assert exc.value.status_code == 422


def test_tg6_skills_body_resolves_rule_with_matching_digest(
    skills_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    rule_root = tmp_path / "docs" / "agent-guides" / "rules"
    rule_root.mkdir(parents=True)
    rule_file = rule_root / "system-conduct.md"
    rule_file.write_text("# System Conduct\nSubstantive rule body.", encoding="utf-8")
    source_uri = "docs/agent-guides/rules/system-conduct.md"
    _insert_rule(
        skills_conn,
        "rule:system-conduct",
        applicable_agents=["*"],
        source_uri=source_uri,
    )
    skills_conn.commit()
    with (
        patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn),
        patch("cortex_store.routes.boot._skill_trigger._FILES_ROOT", tmp_path),
    ):
        out = get_skill_body(id="rule:system-conduct", expected_digest=None)
    assert "Substantive rule body" in out["body"]
    with patch("cortex_store.routes.boot._skill_trigger._FILES_ROOT", tmp_path):
        assert out["digest"] == body_digest(source_uri, "system-conduct")


def test_tg8_rule_capability_required_excludes_inline_only() -> None:
    def _seed() -> sqlite3.Connection:
        conn = _conn()
        _insert_rule(
            conn,
            "rule:mcp-only",
            applicable_agents=["*"],
            capabilities_required=["mcp_fs"],
        )
        conn.commit()
        return conn

    with patch("cortex_store.routes.skills.cortex_conn", return_value=_seed()):
        inline = get_skills(limit=50, layer="rules", for_agent="grok-api-multi")
    with patch("cortex_store.routes.skills.cortex_conn", return_value=_seed()):
        mcp = get_skills(limit=50, layer="rules", for_agent="gpt-cursor")
    inline_ids = {i["id"] for i in inline["items"]}
    mcp_ids = {i["id"] for i in mcp["items"]}
    assert "rule:mcp-only" not in inline_ids
    assert "rule:mcp-only" in mcp_ids


def test_tg9_skills_index_exposes_delivery_priority(
    skills_conn: sqlite3.Connection,
) -> None:
    _insert_skill(
        skills_conn,
        "agent_skill:priority-test",
        applicable_agents=["*"],
        source_uri=None,
    )
    skills_conn.execute(
        "UPDATE entities SET attributes = ? WHERE id = ?",
        (
            json.dumps({"applicable_agents": ["*"], "delivery_priority": 42}),
            "agent_skill:priority-test",
        ),
    )
    skills_conn.commit()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=skills_conn):
        body = get_skills(limit=50, layer="all", for_agent="claude-web")
    item = next(i for i in body["items"] if i["id"] == "agent_skill:priority-test")
    assert item["delivery_priority"] == 42


def test_todo_fail_open_preserved_with_type_scoped_flip(
    skills_conn: sqlite3.Connection,
) -> None:
    _insert_entity(skills_conn, "todo:no-partition", "todo", applicable_agents=None)
    _insert_entity(
        skills_conn, "todo:explicit", "todo", applicable_agents=["claude-cursor"]
    )
    skills_conn.commit()
    out = list_entities_impl(
        skills_conn, entity_type="todo", for_agent="claude-web", limit=50
    )
    ids = {item["id"] for item in out["items"]}
    assert "todo:no-partition" in ids
    assert "todo:explicit" not in ids
