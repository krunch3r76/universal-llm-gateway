"""Shared migrated-DB fixtures for intent=card test groups.

Extracted from the original ``test_intent_card.py`` per the required-case
grouping split (SLOC waiver assertion 8521 on ``spec:cortex-v2.4``).
Each test file under ``test_intent_card_*.py`` covers one of the six
required cases from ``todo:cortex-v24-slice2-adapters``; this module
holds row-insertion helpers they all share.

Not a public API of cortex_store — leading underscore marks the
test-only role.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Head-schema connection for intent-card integration tests."""
    return migrated_conn


def make_conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Return *migrated_conn* — callers should prefer the ``conn`` fixture."""
    return migrated_conn


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
    confidence_band = (
        status if status in {"unsubstantiated", "provisional", "confirmed"} else None
    )
    lifecycle = status if status in {"merged", "deprecated", "reaped"} else None
    conn.execute(
        "INSERT INTO entities (id, type, name, description, confidence_band, lifecycle, "
        "workflow_state, attributes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            name,
            description,
            confidence_band,
            lifecycle,
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
