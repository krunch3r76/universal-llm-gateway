"""Shared in-memory SQLite fixtures for intent=card test groups.

Extracted from the original ``test_intent_card.py`` per the required-case
grouping split (SLOC waiver assertion 8521 on ``spec:cortex-v2.4``).
Each test file under ``test_intent_card_*.py`` covers one of the six
required cases from ``todo:cortex-v24-slice2-adapters``; this module
holds the schema fixture and row-insertion helpers they all share.

Not a public API of cortex_store — leading underscore marks the
test-only role.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def make_conn() -> sqlite3.Connection:
    """In-memory SQLite mirroring the columns the impl reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            workflow_state TEXT,
            attributes TEXT,
            aliases TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            evidence TEXT,
            evidence_uris TEXT,
            seeded_by TEXT,
            chunk_id INTEGER,
            derivation_type TEXT,
            reasoning_summary TEXT,
            is_atomic INTEGER DEFAULT 1,
            is_decontextualized INTEGER DEFAULT 1,
            observed_at TEXT,
            valid_from TEXT,
            valid_until TEXT,
            confidence_score REAL,
            superseded_by INTEGER,
            review_status TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            review_notes TEXT,
            resolution_status TEXT,
            fulfillment_assertion_id INTEGER,
            quality_score REAL,
            prospective_summary TEXT,
            events_json TEXT,
            artifact_uri TEXT,
            artifact_storage TEXT,
            entrenchment_score REAL,
            created_at TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            type TEXT NOT NULL,
            role TEXT,
            strength REAL,
            evidence TEXT,
            chunk_id INTEGER,
            valid_from TEXT,
            valid_until TEXT,
            source_uri TEXT,
            session_id TEXT,
            agent TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT
        );
        CREATE TABLE session_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node TEXT,
            to_node TEXT,
            edge_type TEXT,
            valid_until TEXT,
            created_at TEXT
        );
        CREATE TABLE entity_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT,
            agent TEXT,
            operation TEXT,
            source TEXT,
            session_id TEXT
        );
        """
    )
    return conn


def insert_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    name: str = "Test entity",
    description: str = "Test description.",
    status: str = "confirmed",
    workflow_state: str | None = None,
    attributes: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, status, workflow_state, "
        "attributes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            name,
            description,
            status,
            workflow_state,
            attributes,
            now,
            now,
        ),
    )
    conn.commit()


def insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    confidence: str = "believed",
    superseded_by: int | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (entity_id, claim, confidence, superseded_by, now),
    )
    conn.commit()
    return int(cur.lastrowid or 0)
