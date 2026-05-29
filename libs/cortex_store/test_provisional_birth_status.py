"""P1 regression tests — type-scoped provisional birth-status (thread 1116).

Verifies ``_PROVISIONAL_BIRTH_TYPES`` logic added to ``create_entity_impl``:

  1. ``decision`` created without explicit status → born ``provisional``
  2. ``decision`` created with explicit ``status='confirmed'`` → stays ``confirmed``
     (operator-supplied value always wins)
  3. Non-decision type (``project``) created without status → still ``confirmed``
     (no regression for types outside ``_PROVISIONAL_BIRTH_TYPES``)
"""

from __future__ import annotations

import sqlite3

from cortex_store.entity_crud import create_entity_impl

# ---------------------------------------------------------------------------
# Minimal schema — graceful-degradation helpers in workflow_state,
# type_schemas, and entity_aliases tolerate absent optional tables.
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT,
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
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_decision_without_status_born_provisional() -> None:
    """A decision created with no explicit status MUST be born provisional."""
    c = _conn()
    result = create_entity_impl(
        c, {"id": "decision:test-prov", "type": "decision", "name": "Test"}
    )
    assert result["status"] == "provisional", (
        f"expected 'provisional', got {result['status']!r}"
    )


def test_decision_with_explicit_confirmed_stays_confirmed() -> None:
    """An explicitly-supplied status='confirmed' MUST win over the provisional default."""
    c = _conn()
    result = create_entity_impl(
        c,
        {
            "id": "decision:test-conf",
            "type": "decision",
            "name": "Test",
            "status": "confirmed",
        },
    )
    assert result["status"] == "confirmed", (
        f"expected 'confirmed', got {result['status']!r}"
    )


def test_non_decision_without_status_born_confirmed() -> None:
    """Non-decision types outside _PROVISIONAL_BIRTH_TYPES MUST still default to confirmed."""
    c = _conn()
    result = create_entity_impl(
        c, {"id": "project:test-proj", "type": "project", "name": "Test"}
    )
    assert result["status"] == "confirmed", (
        f"expected 'confirmed', got {result['status']!r}"
    )
