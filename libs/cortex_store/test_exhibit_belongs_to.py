"""Unit tests for exhibit→case ``belongs_to`` enforcement (spec § 1.3).

Covers ``libs/cortex_store/entity_exhibit_lint.py`` and its wiring into
``entity_crud.create_entity_impl``:

  * ID-grammar rejection: missing prefix, missing slash, empty slugs.
  * Parent-case lookup: missing case → 422, deprecated case → 422.
  * Happy path: exhibit create succeeds and the ``belongs_to``
    relationship row is inserted in the same transaction.
  * Non-exhibit types are unaffected (hook is a no-op).
  * Graceful degradation when migration 038 hasn't been applied
    (relationship_types table lacks `belongs_to`): validation still
    runs; relationship insert is skipped.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_exhibit_lint import (
    enforce_exhibit_belongs_to,
    parse_exhibit_case_id,
)


def _fresh_conn(*, with_belongs_to_type: bool = True) -> sqlite3.Connection:
    """In-memory DB with the minimum schema for entity_create + relationships."""
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
            aliases TEXT,
            attributes TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE entity_aliases (
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (entity_id, alias),
            UNIQUE (entity_type, alias)
        );
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT,
            inverse TEXT,
            is_transitive INTEGER DEFAULT 0,
            is_symmetric INTEGER DEFAULT 0,
            from_type TEXT,
            to_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            role TEXT,
            strength REAL,
            evidence TEXT,
            chunk_id INTEGER,
            valid_from TEXT,
            valid_until TEXT,
            source_uri TEXT,
            session_id TEXT,
            agent TEXT,
            created_at TEXT,
            updated_at TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    if with_belongs_to_type:
        conn.execute(
            "INSERT INTO relationship_types (type, description) VALUES (?, ?)",
            ("belongs_to", "Child belongs to parent container"),
        )
    return conn


def _seed_case(
    conn: sqlite3.Connection,
    *,
    case_id: str = "case:boe19p-flintridge-appeal-2026",
    status: str = "confirmed",
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status, retention_policy, "
        "created_at, updated_at) "
        "VALUES (?, 'case', ?, ?, 'permanent', '2026-05-13', '2026-05-13')",
        (case_id, "Test Case", status),
    )


# ---------------------------------------------------------------------------
# parse_exhibit_case_id
# ---------------------------------------------------------------------------


def test_parse_exhibit_case_id_happy_path() -> None:
    case_id = parse_exhibit_case_id(
        "exhibit:boe19p-flintridge-appeal-2026/supplemental-notice-2026-01-16"
    )
    assert case_id == "case:boe19p-flintridge-appeal-2026"


def test_parse_exhibit_case_id_missing_prefix_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_exhibit_case_id("legal_source:rtc-63.2")
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "exhibit_id_grammar_invalid"


def test_parse_exhibit_case_id_missing_slash_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_exhibit_case_id("exhibit:no-separator-here")
    assert exc.value.status_code == 422


def test_parse_exhibit_case_id_empty_slug_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_exhibit_case_id("exhibit:/orphan-slug")
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        parse_exhibit_case_id("exhibit:case-slug/")


# ---------------------------------------------------------------------------
# enforce_exhibit_belongs_to (validation only — no relationship insert)
# ---------------------------------------------------------------------------


def test_enforce_returns_none_for_non_exhibit_types() -> None:
    conn = _fresh_conn()
    assert enforce_exhibit_belongs_to(
        conn, entity_id="person:foo", entity_type="person"
    ) is None
    assert enforce_exhibit_belongs_to(
        conn, entity_id="case-law:larson-v-duca-1989", entity_type="case-law"
    ) is None


def test_enforce_returns_case_id_when_case_exists() -> None:
    conn = _fresh_conn()
    _seed_case(conn)
    case_id = enforce_exhibit_belongs_to(
        conn,
        entity_id="exhibit:boe19p-flintridge-appeal-2026/note-2026-01-16",
        entity_type="exhibit",
    )
    assert case_id == "case:boe19p-flintridge-appeal-2026"


def test_enforce_rejects_missing_parent_case() -> None:
    conn = _fresh_conn()
    with pytest.raises(HTTPException) as exc:
        enforce_exhibit_belongs_to(
            conn,
            entity_id="exhibit:nonexistent-case/note-2026-01-16",
            entity_type="exhibit",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "exhibit_parent_case_missing"
    assert exc.value.detail["case_id"] == "case:nonexistent-case"


def test_enforce_rejects_deprecated_parent_case() -> None:
    conn = _fresh_conn()
    _seed_case(conn, status="deprecated")
    with pytest.raises(HTTPException) as exc:
        enforce_exhibit_belongs_to(
            conn,
            entity_id="exhibit:boe19p-flintridge-appeal-2026/note-2026-01-16",
            entity_type="exhibit",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "exhibit_parent_case_deprecated"


# ---------------------------------------------------------------------------
# create_entity_impl end-to-end
# ---------------------------------------------------------------------------


def test_create_exhibit_inserts_belongs_to_relationship() -> None:
    conn = _fresh_conn()
    _seed_case(conn)
    result = create_entity_impl(
        conn,
        {
            "id": "exhibit:boe19p-flintridge-appeal-2026/note-2026-01-16",
            "type": "exhibit",
            "name": "Exhibit 2 — Notice",
            "attributes": {
                "exhibit_number": "2",
                "document_kind": "notice",
                "issuer": "Santa Clara County Assessor",
                "document_date": "2026-01-16",
                "authentication_basis": "mailed_original",
            },
        },
    )
    assert result["id"] == "exhibit:boe19p-flintridge-appeal-2026/note-2026-01-16"
    rels = conn.execute(
        "SELECT type, from_entity, to_entity, active FROM relationships"
    ).fetchall()
    assert len(rels) == 1
    assert rels[0]["type"] == "belongs_to"
    assert rels[0]["from_entity"] == (
        "exhibit:boe19p-flintridge-appeal-2026/note-2026-01-16"
    )
    assert rels[0]["to_entity"] == "case:boe19p-flintridge-appeal-2026"
    assert rels[0]["active"] == 1


def test_create_exhibit_rejects_missing_case() -> None:
    conn = _fresh_conn()
    # No case seeded.
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(
            conn,
            {
                "id": "exhibit:nonexistent-case/note",
                "type": "exhibit",
                "name": "Orphan exhibit",
                "attributes": {
                    "exhibit_number": "9",
                    "document_kind": "notice",
                    "issuer": "Test",
                    "document_date": "2026-01-16",
                    "authentication_basis": "screenshot",
                },
            },
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "exhibit_parent_case_missing"


def test_create_non_exhibit_does_not_insert_belongs_to() -> None:
    conn = _fresh_conn()
    create_entity_impl(
        conn,
        {
            "id": "person:test",
            "type": "person",
            "name": "Test Person",
        },
    )
    rels = conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()
    assert rels["n"] == 0


def test_graceful_when_belongs_to_type_unregistered() -> None:
    """Pre-migration-038 sandboxes: validation runs, relationship skipped."""
    conn = _fresh_conn(with_belongs_to_type=False)
    _seed_case(conn)
    # Validation still happens — missing case still rejects.
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(
            conn,
            {
                "id": "exhibit:nonexistent-case/note",
                "type": "exhibit",
                "name": "Orphan",
                "attributes": {
                    "exhibit_number": "1",
                    "document_kind": "notice",
                    "issuer": "Test",
                    "document_date": "2026-01-16",
                    "authentication_basis": "screenshot",
                },
            },
        )
    assert exc.value.status_code == 422
    # Happy path: entity created, no relationship row inserted (type unregistered).
    create_entity_impl(
        conn,
        {
            "id": "exhibit:boe19p-flintridge-appeal-2026/note",
            "type": "exhibit",
            "name": "OK",
            "attributes": {
                "exhibit_number": "1",
                "document_kind": "notice",
                "issuer": "Test",
                "document_date": "2026-01-16",
                "authentication_basis": "screenshot",
            },
        },
    )
    rels = conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()
    assert rels["n"] == 0
