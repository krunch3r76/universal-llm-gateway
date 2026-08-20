"""Regression: missing entity on card path raises HTTP 404, not UnboundLocalError."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException, status

from cortex_store.card import get_entity_card


@pytest.fixture()
def card_conn() -> sqlite3.Connection:
    """Minimal schema — 404 fires before assertions/relationships are queried."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            workflow_state TEXT,
            attributes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def test_get_entity_card_missing_entity_raises_404(
    card_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(HTTPException) as exc:
        get_entity_card(card_conn, entity_id="entity:does-not-exist")
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert "Entity not found" in str(exc.value.detail)
