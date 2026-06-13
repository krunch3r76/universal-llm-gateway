"""Schema, identity-key, and aggregate-derivation tests for B3 delivery audit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from event_store.delivery_audit_registry import (
    REGISTRY_SCHEMA_VERSION,
    VALID_ARTIFACT_CLASSES,
    VALID_PROJECTION_SURFACES,
    artifact_identity_key,
    class_qualified_guidance_resource_key,
    connect,
    content_digest,
    derive_aggregate_audit_status,
    derive_guidance_resource_key,
    derive_token_rollups,
    ensure_schema,
    guidance_projection_surface,
    guidance_resource_key,
    new_artifact_record_id,
    new_audit_id,
    validate_whole_doc_reason,
)
from event_store.delivery_audit_schema import _TOKEN_LOCALITY_COLUMN_DEFS

TOKEN_LOCALITY_COLUMN_NAMES = [name for name, _ in _TOKEN_LOCALITY_COLUMN_DEFS]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def registry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db_path = tmp_path / "delivery-audit.db"
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.commit()
    return db_path


def _insert_parent(
    conn: sqlite3.Connection,
    *,
    audit_id: str,
    execution_id: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    aggregate_audit_status: str = "unaudited",
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO delivery_audits (
            audit_id, execution_id, request_id, dispatch_id,
            audit_opened_at, aggregate_audit_status,
            registry_schema_version, audit_policy_version,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            execution_id,
            request_id,
            dispatch_id,
            now,
            aggregate_audit_status,
            REGISTRY_SCHEMA_VERSION,
            "policy-v1",
            now,
            now,
        ),
    )


def _insert_child(
    conn: sqlite3.Connection,
    *,
    audit_id: str,
    artifact_sequence: int,
    artifact_class: str = "http_skill_body",
    artifact_id: str = "agent-skills/consult-routing.md#Implement lane",
    artifact_version: str | None = None,
    delivery_step: str = "dispatch_packet",
    recipient_scope: str | None = "cursor-sdk",
    audit_status: str = "unaudited",
    affordance_kind: str | None = None,
    guidance_resource_key: str | None = None,
    projection_surface: str | None = None,
    content_digest_value: str | None = None,
    delivered_bytes: int | None = None,
    delivered_tokens: int | None = None,
    fetch_scope: str | None = None,
    token_category: str | None = None,
    is_duplicate: int | None = None,
    dedup_scope: str | None = None,
    whole_doc_reason: str | None = None,
    restated_overlap_tokens: int | None = None,
) -> None:
    now = _now()
    identity = artifact_identity_key(
        audit_id=audit_id,
        artifact_class=artifact_class,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        delivery_step=delivery_step,
        recipient_scope=recipient_scope,
    )
    conn.execute(
        """
        INSERT INTO delivered_artifacts (
            artifact_record_id, audit_id, artifact_sequence,
            artifact_identity_key, artifact_class, artifact_id,
            artifact_version, delivery_step, recipient_scope,
            audit_status, affordance_kind,
            guidance_resource_key, projection_surface, content_digest,
            delivered_bytes, delivered_tokens, fetch_scope, token_category,
            is_duplicate, dedup_scope, whole_doc_reason, restated_overlap_tokens,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_artifact_record_id(),
            audit_id,
            artifact_sequence,
            identity,
            artifact_class,
            artifact_id,
            artifact_version,
            delivery_step,
            recipient_scope,
            audit_status,
            affordance_kind,
            guidance_resource_key,
            projection_surface,
            content_digest_value,
            delivered_bytes,
            delivered_tokens,
            fetch_scope,
            token_category,
            is_duplicate,
            dedup_scope,
            whole_doc_reason,
            restated_overlap_tokens,
            now,
            now,
        ),
    )


def test_schema_initializes_under_data_dir(registry_db: Path) -> None:
    assert registry_db.exists()
    with connect(registry_db) as conn:
        parent_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "delivery_audits" in parent_tables
    assert "delivered_artifacts" in parent_tables
    assert "guidance_workflow_summaries" in parent_tables


def test_parent_accepts_nullable_correlation_keys(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(
            conn,
            audit_id=audit_id,
            execution_id=None,
            request_id=None,
            dispatch_id=None,
        )
        conn.commit()
        row = conn.execute(
            "SELECT execution_id, request_id, dispatch_id FROM delivery_audits WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None


def test_parent_rejects_duplicate_non_null_execution_id(registry_db: Path) -> None:
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=new_audit_id(), execution_id="exec-1")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_parent(conn, audit_id=new_audit_id(), execution_id="exec-1")
            conn.commit()


def test_child_rejects_unknown_artifact_class(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                artifact_class="unknown_artifact",
            )
            conn.commit()


def test_child_rejects_unknown_audit_status(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                audit_status="partially-clean",
            )
            conn.commit()


def test_child_accepts_extension_open_affordance_kind(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        _insert_child(
            conn,
            audit_id=audit_id,
            artifact_sequence=1,
            artifact_class="provider_affordance_surface",
            artifact_id="openai/gpt-5/advisor",
            affordance_kind="custom_future_affordance",
        )
        conn.commit()
        row = conn.execute(
            "SELECT affordance_kind FROM delivered_artifacts WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "custom_future_affordance"


def test_child_identity_supports_guidance_slice_artifact_id(registry_db: Path) -> None:
    audit_id = new_audit_id()
    slice_id = "agent-skills/consult-routing.md#Implement lane"
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        _insert_child(
            conn,
            audit_id=audit_id,
            artifact_sequence=1,
            artifact_id=slice_id,
        )
        conn.commit()
        row = conn.execute(
            "SELECT artifact_id FROM delivered_artifacts WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == slice_id


@pytest.mark.parametrize(
    ("artifact_version", "recipient_scope"),
    [
        (None, None),
        ("v1", "cursor-sdk"),
        ("", ""),
    ],
)
def test_artifact_identity_key_is_deterministic_for_nullable_inputs(
    artifact_version: str | None,
    recipient_scope: str | None,
) -> None:
    kwargs = {
        "audit_id": "audit-1",
        "artifact_class": "http_rule_body",
        "artifact_id": "rules/core.md",
        "delivery_step": "boot",
        "artifact_version": artifact_version,
        "recipient_scope": recipient_scope,
    }
    assert artifact_identity_key(**kwargs) == artifact_identity_key(**kwargs)


def test_artifact_identity_key_differs_for_distinct_tuples() -> None:
    base = {
        "audit_id": "audit-1",
        "artifact_class": "http_rule_body",
        "artifact_id": "rules/core.md",
        "delivery_step": "boot",
    }
    key_a = artifact_identity_key(**base, recipient_scope="a")
    key_b = artifact_identity_key(**base, recipient_scope="b")
    assert key_a != key_b


def test_guidance_resource_key_normalizes_section_anchor() -> None:
    assert (
        guidance_resource_key("consult-routing", "Implement lane")
        == "guidance:consult-routing#implement-lane"
    )


@pytest.mark.parametrize(
    "artifact_id",
    [
        "agent-skills/consult-routing.md#Implement lane",
        "cortex://agent-skills/consult-routing.md#Implement lane",
        "docs/agent-guides/skills/consult-routing.md#Implement lane",
        (
            "workspaces://universal-llm-gateway/docs/agent-guides/skills/"
            "consult-routing.md#Implement lane"
        ),
        "docs/agent-guides/rules/consult-routing.md#Implement lane",
        ".cursor/skills/consult-routing/SKILL.md#Implement lane",
    ],
)
def test_derive_guidance_resource_key_dedups_equivalent_guidance_paths(
    artifact_id: str,
) -> None:
    assert (
        derive_guidance_resource_key(
            artifact_class="http_skill_body",
            artifact_id=artifact_id,
        )
        == "guidance:consult-routing#implement-lane"
    )


def test_derive_guidance_resource_key_keeps_sections_distinct() -> None:
    first = derive_guidance_resource_key(
        artifact_class="http_skill_body",
        artifact_id="agent-skills/consult-routing.md#Implement lane",
    )
    second = derive_guidance_resource_key(
        artifact_class="http_skill_body",
        artifact_id="agent-skills/consult-routing.md#Codified bug reports",
    )
    assert first != second


@pytest.mark.parametrize(
    ("resource_class", "name", "expected"),
    [
        ("boot-card", "Agent Skills", "guidance:boot-card#agent-skills"),
        ("tool-descriptor", "cortex", "guidance:tool-descriptor#cortex"),
        ("mcp-schema", "cortex", "guidance:mcp-schema#cortex"),
        (
            "provider-affordance",
            "advisor",
            "guidance:provider-affordance#advisor",
        ),
    ],
)
def test_class_qualified_guidance_resource_key(
    resource_class: str,
    name: str,
    expected: str,
) -> None:
    assert class_qualified_guidance_resource_key(resource_class, name) == expected


def test_derive_guidance_resource_key_for_non_document_surfaces() -> None:
    assert (
        derive_guidance_resource_key(
            artifact_class="boot_card_block",
            artifact_id="Agent Skills",
        )
        == "guidance:boot-card#agent-skills"
    )
    assert (
        derive_guidance_resource_key(
            artifact_class="tool_fol_descriptor",
            artifact_id="cortex",
        )
        == "guidance:tool-descriptor#cortex"
    )
    assert (
        derive_guidance_resource_key(
            artifact_class="tool_fol_descriptor",
            artifact_id="cortex",
            projection_surface="mcp_schema",
        )
        == "guidance:mcp-schema#cortex"
    )
    assert (
        derive_guidance_resource_key(
            artifact_class="provider_affordance_surface",
            artifact_id="openai/gpt-5/advisor",
            affordance_kind="advisor",
        )
        == "guidance:provider-affordance#advisor"
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "artifact_class": "http_skill_body",
                "source_uri": "cortex://agent-skills/consult-routing.md",
            },
            "cortex",
        ),
        (
            {
                "artifact_class": "http_skill_body",
                "source_uri": (
                    "workspaces://universal-llm-gateway/docs/agent-guides/"
                    "skills/consult-routing.md"
                ),
            },
            "workspaces_doc",
        ),
        ({"artifact_class": "http_rule_body"}, "generated_rule"),
        ({"artifact_class": "http_skill_body"}, "generated_skill"),
        ({"artifact_class": "boot_card_block"}, "boot_card_block"),
        ({"artifact_class": "tool_fol_descriptor"}, "tool_descriptor"),
        (
            {"artifact_class": "tool_fol_descriptor", "tool_surface": "mcp_schema"},
            "mcp_schema",
        ),
        (
            {"artifact_class": "provider_affordance_surface"},
            "provider_affordance_surface",
        ),
    ],
)
def test_guidance_projection_surface_mapping(
    kwargs: dict[str, str],
    expected: str,
) -> None:
    assert guidance_projection_surface(**kwargs) == expected
    assert expected in VALID_PROJECTION_SURFACES


@pytest.mark.parametrize(
    ("child_statuses", "covered", "registry_write_failed", "expected"),
    [
        ([], False, False, "not-applicable"),
        ([], True, False, "unaudited"),
        (["audited-clean"], True, False, "audited-clean"),
        (["audited-clean", "not-applicable"], True, False, "audited-clean"),
        (["unaudited"], True, False, "unaudited"),
        (["audit-divergent"], True, False, "audit-divergent"),
        (["audit-divergent", "write-failed"], True, False, "write-failed"),
        (["write-failed"], True, False, "write-failed"),
        (["not-applicable", "not-applicable"], True, False, "not-applicable"),
        ([], True, True, "write-failed"),
    ],
)
def test_derive_aggregate_audit_status(
    child_statuses: list[str],
    covered: bool,
    registry_write_failed: bool,
    expected: str,
) -> None:
    assert (
        derive_aggregate_audit_status(
            child_statuses,
            covered=covered,
            registry_write_failed=registry_write_failed,
        )
        == expected
    )


def test_derive_aggregate_audit_status_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown audit status"):
        derive_aggregate_audit_status(["bogus"], covered=True)


def test_artifact_classes_include_boot_card_block_for_baseline_traces() -> None:
    assert "boot_card_block" in VALID_ARTIFACT_CLASSES


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_ensure_schema_adds_token_locality_columns(registry_db: Path) -> None:
    with connect(registry_db) as conn:
        columns = _column_names(conn, "delivered_artifacts")
    for name in TOKEN_LOCALITY_COLUMN_NAMES:
        assert name in columns


def _create_b3_only_db(db_path: Path) -> sqlite3.Connection:
    """Create a pre-migration B3 registry without token-locality columns."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE delivery_audits (
            audit_id TEXT PRIMARY KEY,
            execution_id TEXT,
            request_id TEXT,
            dispatch_id TEXT,
            pipeline_id TEXT,
            dispatch_surface TEXT,
            dispatch_contract TEXT,
            dispatch_role TEXT,
            dispatch_seat TEXT,
            provider TEXT,
            model TEXT,
            dispatch_thread_id TEXT,
            agent_bus_thread_id TEXT,
            caller_agent TEXT,
            transcript_id TEXT,
            execution_started_at TEXT,
            execution_completed_at TEXT,
            audit_opened_at TEXT NOT NULL,
            audit_finalized_at TEXT,
            aggregate_audit_status TEXT NOT NULL,
            aggregate_reason_code TEXT,
            aggregate_reason TEXT,
            artifact_count INTEGER NOT NULL DEFAULT 0,
            auditable_artifact_count INTEGER NOT NULL DEFAULT 0,
            registry_schema_version TEXT NOT NULL,
            audit_policy_version TEXT NOT NULL,
            producer_version TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE delivered_artifacts (
            artifact_record_id TEXT PRIMARY KEY,
            audit_id TEXT NOT NULL,
            artifact_sequence INTEGER NOT NULL,
            artifact_identity_key TEXT NOT NULL UNIQUE,
            artifact_class TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_version TEXT,
            artifact_revision TEXT,
            artifact_title TEXT,
            delivery_step TEXT NOT NULL,
            recipient_scope TEXT,
            recipient_agent TEXT,
            recipient_surface TEXT,
            source_uri TEXT,
            source_content_hash TEXT,
            rendered_uri TEXT,
            rendered_content_hash TEXT,
            rendered_content_length INTEGER,
            rendered_mime_type TEXT,
            audit_status TEXT NOT NULL,
            audit_reason_code TEXT,
            audit_reason TEXT,
            audit_checker TEXT,
            audit_checker_version TEXT,
            audit_checked_at TEXT,
            audit_evidence_uris TEXT,
            delivery_attempted_at TEXT,
            delivery_succeeded_at TEXT,
            delivery_failed_at TEXT,
            delivery_error_code TEXT,
            delivery_error TEXT,
            write_attempted_at TEXT,
            write_succeeded_at TEXT,
            write_failed_at TEXT,
            write_error_code TEXT,
            write_error TEXT,
            provider TEXT,
            model TEXT,
            dispatch_contract TEXT,
            dispatch_role TEXT,
            affordance_kind TEXT,
            tool_surface TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return conn


def test_ensure_schema_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-delivery-audit.db"
    conn = _create_b3_only_db(db_path)
    try:
        assert not set(TOKEN_LOCALITY_COLUMN_NAMES) <= _column_names(
            conn, "delivered_artifacts"
        )
        ensure_schema(conn)
        conn.commit()
        migrated = _column_names(conn, "delivered_artifacts")
        for name in TOKEN_LOCALITY_COLUMN_NAMES:
            assert name in migrated
        ensure_schema(conn)
        conn.commit()
        assert migrated == _column_names(conn, "delivered_artifacts")
    finally:
        conn.close()


def test_child_persists_token_locality_fields(registry_db: Path) -> None:
    audit_id = new_audit_id()
    digest = content_digest("guidance body")
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        _insert_child(
            conn,
            audit_id=audit_id,
            artifact_sequence=1,
            guidance_resource_key="cortex://agent-skills/foo.md",
            projection_surface="cortex",
            content_digest_value=digest,
            delivered_bytes=1200,
            delivered_tokens=300,
            fetch_scope="section",
            token_category="fetched_guidance",
            is_duplicate=0,
            dedup_scope="turn",
            whole_doc_reason=None,
            restated_overlap_tokens=12,
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT guidance_resource_key, projection_surface, content_digest,
                   delivered_bytes, delivered_tokens, fetch_scope, token_category,
                   is_duplicate, dedup_scope, whole_doc_reason, restated_overlap_tokens
            FROM delivered_artifacts WHERE audit_id = ?
            """,
            (audit_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "cortex://agent-skills/foo.md"
    assert row[1] == "cortex"
    assert row[2] == digest
    assert row[3] == 1200
    assert row[4] == 300
    assert row[5] == "section"
    assert row[6] == "fetched_guidance"
    assert row[7] == 0
    assert row[8] == "turn"
    assert row[9] is None
    assert row[10] == 12


def test_child_rejects_unknown_projection_surface(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                projection_surface="unknown_surface",
            )
            conn.commit()


def test_child_rejects_unknown_fetch_scope(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                fetch_scope="entire_corpus",
            )
            conn.commit()


def test_child_rejects_unknown_token_category(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                token_category="unknown_category",
            )
            conn.commit()


def test_child_rejects_unknown_dedup_scope(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_child(
                conn,
                audit_id=audit_id,
                artifact_sequence=1,
                dedup_scope="campaign",
            )
            conn.commit()


def test_child_accepts_null_token_locality_fields(registry_db: Path) -> None:
    audit_id = new_audit_id()
    with connect(registry_db) as conn:
        _insert_parent(conn, audit_id=audit_id)
        _insert_child(conn, audit_id=audit_id, artifact_sequence=1)
        conn.commit()
        row = conn.execute(
            """
            SELECT guidance_resource_key, projection_surface, content_digest,
                   delivered_bytes, delivered_tokens, fetch_scope, token_category,
                   is_duplicate, dedup_scope, whole_doc_reason, restated_overlap_tokens
            FROM delivered_artifacts WHERE audit_id = ?
            """,
            (audit_id,),
        ).fetchone()
    assert row is not None
    assert all(value is None for value in row)


def test_content_digest_is_deterministic_and_hex() -> None:
    body = "delivered guidance"
    first = content_digest(body)
    second = content_digest(body)
    assert first == second
    assert len(first) == 64
    assert all(ch in "0123456789abcdef" for ch in first)


def test_validate_whole_doc_reason_flags_missing_reason() -> None:
    flagged = validate_whole_doc_reason(
        [
            {
                "artifact_record_id": "rec-1",
                "fetch_scope": "whole_doc",
                "whole_doc_reason": None,
            },
            {
                "artifact_identity_key": "key-2",
                "fetch_scope": "whole_doc",
                "whole_doc_reason": "",
            },
            {
                "artifact_record_id": "rec-3",
                "fetch_scope": "whole_doc",
                "whole_doc_reason": "required for audit",
            },
            {
                "artifact_record_id": "rec-4",
                "fetch_scope": "section",
                "whole_doc_reason": None,
            },
        ]
    )
    assert flagged == ["rec-1", "key-2"]


def test_derive_token_rollups_sums_by_category() -> None:
    rows = [
        {
            "delivered_tokens": 100,
            "token_category": "resident_guidance",
            "delivered_bytes": 400,
            "fetch_scope": "resident",
            "is_duplicate": 0,
            "restated_overlap_tokens": 5,
            "guidance_resource_key": "key-a",
        },
        {
            "delivered_tokens": 50,
            "token_category": "fetched_guidance",
            "delivered_bytes": 800,
            "fetch_scope": "whole_doc",
            "is_duplicate": 1,
            "restated_overlap_tokens": 3,
            "guidance_resource_key": "key-a",
        },
        {
            "delivered_tokens": 20,
            "token_category": "tool_schema_discovery",
            "delivered_bytes": 100,
            "fetch_scope": "section",
            "is_duplicate": 0,
            "restated_overlap_tokens": None,
            "guidance_resource_key": "key-b",
        },
        {
            "delivered_tokens": 10,
            "token_category": "task_corpus",
            "delivered_bytes": 60,
            "fetch_scope": "checklist",
            "is_duplicate": 0,
            "restated_overlap_tokens": 2,
            "guidance_resource_key": "key-b",
        },
        {
            "delivered_tokens": 7,
            "token_category": "transcript_restated_guidance",
            "delivered_bytes": 30,
            "fetch_scope": "synthesized_bundle",
            "is_duplicate": 0,
            "restated_overlap_tokens": 1,
            "guidance_resource_key": "key-c",
        },
    ]
    rollups = derive_token_rollups(rows)
    assert rollups == {
        "resident_guidance_tokens": 100,
        "fetched_guidance_tokens": 50,
        "duplicate_guidance_tokens": 50,
        "tool_schema_tokens": 20,
        "task_corpus_tokens": 10,
        "transcript_restated_tokens": 7,
        "restated_overlap_tokens_total": 11,
        "whole_doc_bytes": 800,
        "section_bytes": 190,
        "trigger_fan_in_count": 2,
    }


def test_derive_token_rollups_null_coalesces_to_zero() -> None:
    rollups = derive_token_rollups(
        [
            {
                "delivered_tokens": None,
                "token_category": "resident_guidance",
                "delivered_bytes": None,
                "fetch_scope": "whole_doc",
                "is_duplicate": None,
                "restated_overlap_tokens": None,
                "guidance_resource_key": "solo",
            }
        ]
    )
    assert rollups["resident_guidance_tokens"] == 0
    assert rollups["duplicate_guidance_tokens"] == 0
    assert rollups["restated_overlap_tokens_total"] == 0
    assert rollups["whole_doc_bytes"] == 0
    assert rollups["trigger_fan_in_count"] == 1


def test_derive_token_rollups_ignores_missing_resource_keys_for_fan_in() -> None:
    rows = [
        {"guidance_resource_key": None, "delivered_tokens": 1},
        {"guidance_resource_key": None, "delivered_tokens": 2},
        {"delivered_tokens": 3},
    ]
    assert derive_token_rollups(rows)["trigger_fan_in_count"] == 0


def test_derive_token_rollups_trigger_fan_in_count() -> None:
    rows = [
        {"guidance_resource_key": "shared", "delivered_tokens": 1},
        {"guidance_resource_key": "shared", "delivered_tokens": 2},
        {"guidance_resource_key": "shared", "delivered_tokens": 3},
        {"guidance_resource_key": "other", "delivered_tokens": 4},
    ]
    assert derive_token_rollups(rows)["trigger_fan_in_count"] == 3


def test_registry_schema_version_is_three() -> None:
    assert REGISTRY_SCHEMA_VERSION == "3"
