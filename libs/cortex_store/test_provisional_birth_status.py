"""Birth-status regression tests — Fork D (G1, thread 1173) + thread 1116.

Fork D flips the confidence axis to DERIVED: the birth default for ordinary
types is ``unsubstantiated`` (not ``confirmed``), and a hand-set confidence-axis
status (``confirmed``/``provisional``/``unsubstantiated``) is FROZEN (ignored).
An explicit lifecycle-axis status (``merged``/``deprecated``/``reaped``) is still
honored. ``_PROVISIONAL_BIRTH_TYPES`` (currently ``{decision}``) keep their
``provisional`` birth for workflow coherence (thread 1116).

  1. ``decision`` created without explicit status → born ``provisional``
  2. ``decision`` created with hand-set ``status='confirmed'`` → frozen, stays
     ``provisional`` (Fork D: confidence is derived, not hand-set)
  3. Non-decision type (``project``) created without status → born
     ``unsubstantiated`` (Fork D default flip)
  4. Non-decision type with hand-set ``confirmed`` → frozen to ``unsubstantiated``
  5. Explicit lifecycle-axis status (``deprecated``) → honored
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


def test_decision_with_hand_set_confirmed_is_frozen() -> None:
    """Fork D: a hand-set confidence-axis status is ignored; decision stays provisional."""
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
    assert result["status"] == "provisional", (
        f"hand-set confirmed must be frozen; expected 'provisional', got {result['status']!r}"
    )


def test_non_decision_without_status_born_unsubstantiated() -> None:
    """Fork D: ordinary types default to 'unsubstantiated', not 'confirmed'."""
    c = _conn()
    result = create_entity_impl(
        c, {"id": "project:test-proj", "type": "project", "name": "Test"}
    )
    assert result["status"] == "unsubstantiated", (
        f"expected 'unsubstantiated', got {result['status']!r}"
    )


def test_non_decision_hand_set_confirmed_frozen_to_unsubstantiated() -> None:
    """Fork D: hand-set confidence-axis status on an ordinary type is frozen."""
    c = _conn()
    result = create_entity_impl(
        c,
        {
            "id": "project:test-frozen",
            "type": "project",
            "name": "Test",
            "status": "confirmed",
        },
    )
    assert result["status"] == "unsubstantiated", (
        f"hand-set confirmed must be frozen; expected 'unsubstantiated', "
        f"got {result['status']!r}"
    )


def test_explicit_lifecycle_status_is_honored() -> None:
    """Fork D: lifecycle-axis status (deprecated) is still settable at birth."""
    c = _conn()
    result = create_entity_impl(
        c,
        {
            "id": "project:test-deprecated",
            "type": "project",
            "name": "Test",
            "status": "deprecated",
        },
    )
    assert result["status"] == "deprecated", (
        f"lifecycle status must be honored; expected 'deprecated', got {result['status']!r}"
    )
