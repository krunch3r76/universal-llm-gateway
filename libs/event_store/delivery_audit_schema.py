"""SQLite schema and connection helpers for the B3 delivery-audit sibling registry.

Schema version 3 adds workflow-summary rows for token-locality baseline campaigns.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_TOKEN_LOCALITY_COLUMN_DEFS: tuple[tuple[str, str], ...] = (
    ("guidance_resource_key", "TEXT"),
    (
        "projection_surface",
        "TEXT CHECK (projection_surface IS NULL OR projection_surface IN "
        "('cortex','workspaces_doc','generated_rule','generated_skill',"
        "'boot_card_block','tool_descriptor','mcp_schema',"
        "'provider_affordance_surface','sidecar_corpus'))",
    ),
    ("content_digest", "TEXT"),
    ("delivered_bytes", "INTEGER"),
    ("delivered_tokens", "INTEGER"),
    (
        "fetch_scope",
        "TEXT CHECK (fetch_scope IS NULL OR fetch_scope IN "
        "('resident','whole_doc','section','checklist','synthesized_bundle'))",
    ),
    (
        "token_category",
        "TEXT CHECK (token_category IS NULL OR token_category IN "
        "('resident_guidance','fetched_guidance','tool_schema_discovery',"
        "'task_corpus','transcript_restated_guidance'))",
    ),
    ("is_duplicate", "INTEGER CHECK (is_duplicate IS NULL OR is_duplicate IN (0,1))"),
    (
        "dedup_scope",
        "TEXT CHECK (dedup_scope IS NULL OR dedup_scope IN ('turn','session'))",
    ),
    ("whole_doc_reason", "TEXT"),
    ("restated_overlap_tokens", "INTEGER"),
)

_TOKEN_LOCALITY_DDL = ",\n    ".join(
    f"{name} {definition}" for name, definition in _TOKEN_LOCALITY_COLUMN_DEFS
)

_DDL = f"""
CREATE TABLE IF NOT EXISTS delivery_audits (
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

    aggregate_audit_status TEXT NOT NULL CHECK (
        aggregate_audit_status IN (
            'audited-clean',
            'audit-divergent',
            'unaudited',
            'not-applicable',
            'write-failed'
        )
    ),
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_delivery_audits_execution
    ON delivery_audits(execution_id)
    WHERE execution_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_delivery_audits_request
    ON delivery_audits(request_id)
    WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_delivery_audits_dispatch
    ON delivery_audits(dispatch_id)
    WHERE dispatch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_delivery_audits_status
    ON delivery_audits(aggregate_audit_status);
CREATE INDEX IF NOT EXISTS idx_delivery_audits_completed
    ON delivery_audits(execution_completed_at);

CREATE TABLE IF NOT EXISTS delivered_artifacts (
    artifact_record_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL REFERENCES delivery_audits(audit_id)
        ON DELETE CASCADE,
    artifact_sequence INTEGER NOT NULL,
    artifact_identity_key TEXT NOT NULL UNIQUE,

    artifact_class TEXT NOT NULL CHECK (
        artifact_class IN (
            'http_rule_body',
            'http_skill_body',
            'boot_card_block',
            'tool_fol_descriptor',
            'provider_affordance_surface'
        )
    ),
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

    audit_status TEXT NOT NULL CHECK (
        audit_status IN (
            'audited-clean',
            'audit-divergent',
            'unaudited',
            'not-applicable',
            'write-failed'
        )
    ),
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

    {_TOKEN_LOCALITY_DDL},

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivered_artifacts_audit
    ON delivered_artifacts(audit_id, artifact_sequence);
CREATE INDEX IF NOT EXISTS idx_delivered_artifacts_status
    ON delivered_artifacts(audit_status);
CREATE INDEX IF NOT EXISTS idx_delivered_artifacts_class
    ON delivered_artifacts(artifact_class, artifact_id);
CREATE INDEX IF NOT EXISTS idx_delivered_artifacts_affordance
    ON delivered_artifacts(affordance_kind)
    WHERE affordance_kind IS NOT NULL;

CREATE TABLE IF NOT EXISTS guidance_workflow_summaries (
    workflow_summary_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL REFERENCES delivery_audits(audit_id)
        ON DELETE CASCADE,
    execution_id TEXT NOT NULL UNIQUE,
    workflow_class TEXT NOT NULL CHECK (
        workflow_class IN (
            'simple_edit',
            'debugging',
            'review',
            'handoff_pickup',
            'session_close'
        )
    ),
    phase TEXT NOT NULL CHECK (phase IN ('baseline', 'post_change')),
    campaign_id TEXT NOT NULL,
    seat_substrate TEXT NOT NULL CHECK (seat_substrate IN ('cursor', 'web-claude')),

    input_tokens INTEGER NOT NULL,
    resident_guidance_tokens INTEGER NOT NULL,
    fetched_guidance_tokens INTEGER NOT NULL,
    duplicate_guidance_tokens INTEGER NOT NULL,
    tool_schema_tokens INTEGER NOT NULL,
    task_corpus_tokens INTEGER NOT NULL,
    transcript_restated_tokens INTEGER NOT NULL,
    restated_overlap_tokens_total INTEGER NOT NULL,
    whole_doc_bytes INTEGER NOT NULL,
    section_bytes INTEGER NOT NULL,
    trigger_fan_in_count INTEGER NOT NULL,
    artifact_count INTEGER NOT NULL,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guidance_workflow_campaign
    ON guidance_workflow_summaries(campaign_id, phase, seat_substrate, workflow_class);
CREATE INDEX IF NOT EXISTS idx_guidance_workflow_class
    ON guidance_workflow_summaries(workflow_class, phase);
"""


def _migrate_token_locality_columns(db: sqlite3.Connection) -> None:
    """Add token-locality columns to an existing B3 ``delivered_artifacts`` table."""
    existing = {
        row[1]
        for row in db.execute("PRAGMA table_info(delivered_artifacts)").fetchall()
    }
    for name, definition in _TOKEN_LOCALITY_COLUMN_DEFS:
        if name not in existing:
            db.execute(
                f"ALTER TABLE delivered_artifacts ADD COLUMN {name} {definition}"
            )


def delivery_audit_db_path() -> Path:
    """Return the B3 delivery-audit sibling database path."""
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "delivery-audit.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a configured SQLite connection to the delivery-audit registry."""
    conn = sqlite3.connect(path or delivery_audit_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.commit()
    return conn


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create registry tables and indexes if they do not already exist."""
    owns = conn is None
    db = conn or connect()
    try:
        db.executescript(_DDL)
        _migrate_token_locality_columns(db)
        if owns:
            db.commit()
    finally:
        if owns:
            db.close()
