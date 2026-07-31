"""Lifecycle default-deny on entity listing, /skills/body, and entity_get."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops._detectors.skill_binding import (
    detect_skill_binding_missing,
)
from cortex_store.dispatch_ops.ops_entities import _op_entities, _op_entity_get
from cortex_store.entity_crud import list_entities_impl
from cortex_store.entity_read import get_entity_impl
from cortex_store.routes.boot.recent_mentions import _RECENT_MENTIONS_DEFAULT_EXCLUDE
from cortex_store.routes.skills import get_skill_body

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
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    confidence REAL,
    derivation_type TEXT,
    valid_from TEXT,
    observed_at TEXT,
    predicate_form TEXT,
    evidence_uris TEXT,
    entrenchment_score REAL,
    prospective_summary TEXT,
    created_at TEXT,
    updated_at TEXT,
    superseded_by INTEGER
);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity TEXT NOT NULL,
    to_entity TEXT NOT NULL,
    type TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT
);
CREATE TABLE entity_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT,
    agent TEXT,
    operation TEXT,
    source TEXT,
    session_id TEXT,
    created_at TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    entity_type: str = "agent_skill",
    lifecycle: str | None = "active",
    source_uri: str | None = None,
    attributes: dict[str, object] | None = None,
) -> None:
    now = "2026-06-15T00:00:00Z"
    conn.execute(
        "INSERT INTO entities "
        "(id, type, name, description, source_uri, lifecycle, attributes, created_at, updated_at) "
        "VALUES (?, ?, ?, 'desc', ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            entity_id.split(":", 1)[-1],
            source_uri,
            lifecycle,
            json.dumps(attributes) if attributes is not None else None,
            now,
            now,
        ),
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    "lifecycle",
    ["draft", "deprecated", "retired", "merged", None],
)
def test_list_entities_agent_skill_default_excludes_inactive(
    lifecycle: str | None,
) -> None:
    conn = _conn()
    _insert_entity(conn, "agent_skill:active-one", lifecycle="active")
    _insert_entity(conn, f"agent_skill:inactive-{lifecycle}", lifecycle=lifecycle)
    conn.commit()

    out = list_entities_impl(conn, entity_type="agent_skill", limit=50)
    ids = {item["id"] for item in out["items"]}
    assert ids == {"agent_skill:active-one"}


@pytest.mark.offline
def test_list_entities_agent_skill_include_non_active_returns_all() -> None:
    conn = _conn()
    lifecycles = ["active", "draft", "deprecated", "retired", "merged", None]
    for lc in lifecycles:
        suffix = lc if lc is not None else "null"
        _insert_entity(conn, f"agent_skill:{suffix}", lifecycle=lc)
    conn.commit()

    out = list_entities_impl(
        conn, entity_type="agent_skill", limit=50, include_non_active=True
    )
    ids = {item["id"] for item in out["items"]}
    assert ids == {
        "agent_skill:active",
        "agent_skill:draft",
        "agent_skill:deprecated",
        "agent_skill:retired",
        "agent_skill:merged",
        "agent_skill:null",
    }


@pytest.mark.offline
def test_list_entities_untyped_mixed_rows_filters_only_inactive_skills() -> None:
    conn = _conn()
    _insert_entity(conn, "agent_skill:active-skill", lifecycle="active")
    _insert_entity(conn, "agent_skill:draft-skill", lifecycle="draft")
    _insert_entity(conn, "decision:merged-one", entity_type="decision", lifecycle="merged")
    conn.commit()

    out = list_entities_impl(conn, limit=50)
    ids = {item["id"] for item in out["items"]}
    assert ids == {"agent_skill:active-skill", "decision:merged-one"}


@pytest.mark.offline
def test_op_entities_default_denies_inactive_agent_skill() -> None:
    conn = _conn()
    _insert_entity(conn, "agent_skill:active-one", lifecycle="active")
    _insert_entity(conn, "agent_skill:draft-one", lifecycle="draft")
    conn.commit()

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", return_value=conn):
        out = _op_entities(type="agent_skill", limit=50)
    ids = {item["id"] for item in out["items"]}
    assert ids == {"agent_skill:active-one"}


@pytest.mark.offline
def test_get_skill_body_active_returns_body_and_discoverable(
    tmp_path: Path,
) -> None:
    conn = _conn()
    skill_file = tmp_path / "active.md"
    skill_file.write_text("active body", encoding="utf-8")
    _insert_entity(
        conn,
        "agent_skill:active-body",
        lifecycle="active",
        source_uri=str(skill_file),
    )
    conn.commit()

    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        out = get_skill_body(id="agent_skill:active-body", expected_digest=None)
    assert out["body"] == "active body"
    assert out["discoverable"] is True


@pytest.mark.offline
@pytest.mark.parametrize("lifecycle", ["draft", None])
def test_get_skill_body_inactive_default_withholds_body(lifecycle: str | None) -> None:
    conn = _conn()
    suffix = lifecycle if lifecycle is not None else "null"
    _insert_entity(
        conn,
        f"agent_skill:{suffix}-body",
        lifecycle=lifecycle,
        source_uri=f"workspaces://universal-llm-gateway/.cursor/skills/{suffix}-body/SKILL.md",
    )
    conn.commit()

    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        out = get_skill_body(id=f"agent_skill:{suffix}-body", expected_digest=None)
    assert out["body"] is None
    assert out["discoverable"] is False
    assert out["reason"] == "inactive_lifecycle_withheld"


@pytest.mark.offline
def test_get_skill_body_include_non_active_returns_inactive_body(
    tmp_path: Path,
) -> None:
    conn = _conn()
    skill_file = tmp_path / "draft.md"
    skill_file.write_text("draft body", encoding="utf-8")
    _insert_entity(
        conn,
        "agent_skill:draft-body",
        lifecycle="draft",
        source_uri=str(skill_file),
    )
    conn.commit()

    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        out = get_skill_body(
            id="agent_skill:draft-body",
            expected_digest=None,
            include_non_active=True,
        )
    assert out["body"] == "draft body"
    assert out["discoverable"] is False


@pytest.mark.offline
def test_get_skill_body_rule_unaffected_by_lifecycle_gate(tmp_path: Path) -> None:
    conn = _conn()
    rule_file = tmp_path / "rule.md"
    rule_file.write_text("rule body", encoding="utf-8")
    now = "2026-06-15T00:00:00Z"
    conn.execute(
        "INSERT INTO entities "
        "(id, type, name, description, source_uri, lifecycle, created_at, updated_at) "
        "VALUES ('rule:test-rule', 'rule', 'test-rule', 'desc', ?, 'draft', ?, ?)",
        (str(rule_file), now, now),
    )
    conn.commit()

    with patch("cortex_store.routes.skills.cortex_conn", return_value=conn):
        out = get_skill_body(id="rule:test-rule", expected_digest=None)
    assert out["body"] == "rule body"
    assert out["discoverable"] is True


@pytest.mark.offline
def test_entity_get_stamps_discoverable_on_agent_skill(
    migrated_conn: sqlite3.Connection,
) -> None:
    _insert_entity(migrated_conn, "agent_skill:active-get", lifecycle="active")
    _insert_entity(migrated_conn, "agent_skill:draft-get", lifecycle="draft")
    migrated_conn.commit()

    active = get_entity_impl(migrated_conn, entity_id="agent_skill:active-get")
    inactive = get_entity_impl(migrated_conn, entity_id="agent_skill:draft-get")
    assert active["discoverable"] is True
    assert inactive["discoverable"] is False


@pytest.mark.offline
def test_op_entity_get_stamps_discoverable(migrated_conn: sqlite3.Connection) -> None:
    _insert_entity(migrated_conn, "agent_skill:draft-op", lifecycle="draft")
    migrated_conn.commit()

    with patch(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn",
        return_value=migrated_conn,
    ):
        out = _op_entity_get(entity_id="agent_skill:draft-op")
    assert out["discoverable"] is False


@pytest.mark.offline
def test_recent_mentions_default_excludes_agent_skill() -> None:
    assert "agent_skill" in _RECENT_MENTIONS_DEFAULT_EXCLUDE


@pytest.mark.offline
def test_skill_binding_detector_flags_null_lifecycle_missing_binding() -> None:
    conn = _conn()
    _insert_entity(
        conn,
        "agent_skill:null-binding",
        lifecycle=None,
        attributes={"applicable_agents": ["*"]},
    )
    conn.commit()

    findings = detect_skill_binding_missing(conn)
    assert any(f["subject"] == "agent_skill:null-binding" for f in findings)


@pytest.mark.offline
def test_boot_manifest_skills_hint_documents_include_non_active() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "services/mcp-server/tools/_boot_helpers/_manifest.py"
    )
    text = manifest_path.read_text(encoding="utf-8")
    assert "include_non_active" in text
