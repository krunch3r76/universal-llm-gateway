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

import pytest

from cortex_store.entity_crud import create_entity_impl


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def test_decision_without_status_born_provisional(conn: sqlite3.Connection) -> None:
    """A decision created with no explicit status MUST be born provisional."""
    result = create_entity_impl(
        conn, {"id": "decision:test-prov", "type": "decision", "name": "Test"}
    )
    assert result.get("confidence_band") == "provisional", (
        f"expected confidence_band 'provisional', got {result.get('confidence_band')!r}"
    )


def test_decision_with_hand_set_confirmed_is_frozen(conn: sqlite3.Connection) -> None:
    """Fork D: a hand-set confidence-axis status is ignored; decision stays provisional."""
    result = create_entity_impl(
        conn,
        {
            "id": "decision:test-conf",
            "type": "decision",
            "name": "Test",
            "status": "confirmed",
        },
    )
    assert result.get("confidence_band") == "provisional", (
        f"hand-set confirmed must be frozen; expected 'provisional', "
        f"got {result.get('confidence_band')!r}"
    )


def test_non_decision_without_status_born_unsubstantiated(
    conn: sqlite3.Connection,
) -> None:
    """Fork D: ordinary types default to 'unsubstantiated', not 'confirmed'."""
    result = create_entity_impl(
        conn, {"id": "project:test-proj", "type": "project", "name": "Test"}
    )
    assert result.get("confidence_band") == "unsubstantiated", (
        f"expected 'unsubstantiated', got {result.get('confidence_band')!r}"
    )


def test_non_decision_hand_set_confirmed_frozen_to_unsubstantiated(
    conn: sqlite3.Connection,
) -> None:
    """Fork D: hand-set confidence-axis status on an ordinary type is frozen."""
    result = create_entity_impl(
        conn,
        {
            "id": "project:test-frozen",
            "type": "project",
            "name": "Test",
            "status": "confirmed",
        },
    )
    assert result.get("confidence_band") == "unsubstantiated", (
        f"hand-set confirmed must be frozen; expected 'unsubstantiated', "
        f"got {result.get('confidence_band')!r}"
    )


def test_explicit_lifecycle_status_is_honored(conn: sqlite3.Connection) -> None:
    """Fork D: lifecycle-axis status (deprecated) is still settable at birth."""
    result = create_entity_impl(
        conn,
        {
            "id": "project:test-deprecated",
            "type": "project",
            "name": "Test",
            "status": "deprecated",
        },
    )
    assert result.get("lifecycle") == "deprecated", (
        f"lifecycle status must be honored; expected 'deprecated', "
        f"got {result.get('lifecycle')!r}"
    )
