"""Regression tests for relationship type validation error messages."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import HTTPException, Response

from cortex_store.models import RelationshipCreate
from cortex_store.routes import relationships as rel_mod
from cortex_store.routes.relationships import create_relationship


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    migrated_conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) "
        "VALUES ('related_to', 'General association')"
    )
    migrated_conn.execute(
        "INSERT OR IGNORE INTO session_edge_types (type, description, directional) "
        "VALUES ('relates_to', 'Associative link', 0)"
    )
    migrated_conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name, created_at, updated_at) "
        "VALUES ('project:a', 'project', 'A', 't0', 't0'), "
        "('project:b', 'project', 'B', 't0', 't0')"
    )
    migrated_conn.commit()
    return migrated_conn


@contextmanager
def _patched_relationship_conn(conn: sqlite3.Connection):
    class _ConnCtx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *args: object) -> None:
            return None

    original = rel_mod.cortex_conn
    rel_mod.cortex_conn = lambda: _ConnCtx()  # type: ignore[assignment]
    try:
        yield
    finally:
        rel_mod.cortex_conn = original


def _create(conn: sqlite3.Connection, *, type_id: str) -> None:
    with _patched_relationship_conn(conn):
        create_relationship(
            RelationshipCreate(
                source_id="project:a",
                target_id="project:b",
                type_id=type_id,
            ),
            Response(),
        )


def test_relates_to_edge_type_error_names_related_to(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _create(conn, type_id="relates_to")
    assert exc_info.value.status_code == 404
    detail = str(exc_info.value.detail)
    assert "related_to" in detail
    assert "EDGE type" in detail


def test_related_to_relationship_type_succeeds(conn: sqlite3.Connection) -> None:
    _create(conn, type_id="related_to")


def test_unknown_type_suggests_close_match(conn: sqlite3.Connection) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _create(conn, type_id="relatd_to")
    assert exc_info.value.status_code == 404
    assert "Did you mean" in str(exc_info.value.detail)
    assert "related_to" in str(exc_info.value.detail)
