"""Trait-native SQL predicates and registry defaults (reader pass 686612ed)."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.confidence_field import (
    DEFAULT_CONFIDENCE_FIELD,
    SUPPRESSED_SKILL_LIFECYCLES,
    adoption_in_sql_predicate,
    confidence_band_sql_predicate,
    confidence_field,
    lifecycle_is_value_sql_predicate,
    lifecycle_not_in_sql_predicate,
    lifecycle_not_value_sql_predicate,
    uses_confidence_band_axis,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT,
            status TEXT,
            lifecycle TEXT,
            confidence_band TEXT,
            adoption TEXT
        );
        CREATE TABLE type_confidence_fields (
            entity_type TEXT PRIMARY KEY,
            confidence_field TEXT NOT NULL
        );
        """
    )
    return c


def test_default_confidence_field_is_band() -> None:
    assert DEFAULT_CONFIDENCE_FIELD == "confidence_band"


def test_confidence_band_predicate_single_param() -> None:
    c = _conn()
    c.execute(
        "INSERT INTO entities (id, type, confidence_band) VALUES ('p:1', 'person', 'confirmed')"
    )
    c.execute(
        "INSERT INTO entities (id, type, confidence_band) VALUES ('p:2', 'person', 'provisional')"
    )
    pred = confidence_band_sql_predicate()
    rows = c.execute(f"SELECT id FROM entities WHERE {pred}", ("confirmed",)).fetchall()
    assert [r[0] for r in rows] == ["p:1"]


def test_lifecycle_not_value_trait_only() -> None:
    c = _conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('a', 'person', 'active', 'deprecated');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('b', 'person', 'deprecated', 'active');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('c', 'person', NULL, 'deprecated');
        """
    )
    pred = lifecycle_not_value_sql_predicate("deprecated")
    rows = {
        r[0]
        for r in c.execute(f"SELECT id FROM entities WHERE {pred}", ("deprecated",))
    }
    assert rows == {"a", "c"}


def test_lifecycle_is_value_trait_only() -> None:
    c = _conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('a', 'decision', 'deprecated', 'confirmed');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('b', 'decision', NULL, 'deprecated');
        """
    )
    pred = lifecycle_is_value_sql_predicate("deprecated")
    rows = {
        r[0]
        for r in c.execute(f"SELECT id FROM entities WHERE {pred}", ("deprecated",))
    }
    assert rows == {"a"}


def test_adoption_in_trait_only() -> None:
    c = _conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, adoption, status) VALUES ('d1', 'decision', 'adopted', 'confirmed');
        INSERT INTO entities (id, type, adoption, status) VALUES ('d2', 'decision', NULL, 'confirmed');
        """
    )
    pred = adoption_in_sql_predicate(("adopted", "canonical"), ("confirmed",))
    rows = {
        r[0]
        for r in c.execute(
            f"SELECT id FROM entities WHERE {pred}", ("adopted", "canonical")
        )
    }
    assert rows == {"d1"}


def test_registry_normalizes_legacy_status_value() -> None:
    c = _conn()
    c.execute(
        "INSERT INTO type_confidence_fields (entity_type, confidence_field) VALUES ('person', 'status')"
    )
    assert confidence_field(c, "person") == "confidence_band"
    assert uses_confidence_band_axis(confidence_field(c, "person"))


def test_todo_uses_workflow_state_not_band_axis() -> None:
    c = _conn()
    c.execute(
        "INSERT INTO type_confidence_fields (entity_type, confidence_field) "
        "VALUES ('todo', 'workflow_state')"
    )
    assert not uses_confidence_band_axis(confidence_field(c, "todo"))


@pytest.mark.offline
def test_lifecycle_not_in_excludes_all_suppressed_values() -> None:
    """NOT IN predicate excludes all listed lifecycle values; null and active pass."""
    c = _conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('active', 'person', 'active', 'x');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('null_lc', 'person', NULL, 'x');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('deprecated', 'person', 'deprecated', 'x');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('draft', 'person', 'draft', 'x');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('retired', 'person', 'retired', 'x');
        INSERT INTO entities (id, type, lifecycle, status) VALUES ('merged', 'person', 'merged', 'x');
        """
    )
    pred = lifecycle_not_in_sql_predicate(SUPPRESSED_SKILL_LIFECYCLES)
    rows = {
        r[0]
        for r in c.execute(
            f"SELECT id FROM entities WHERE {pred}",
            SUPPRESSED_SKILL_LIFECYCLES,
        )
    }
    assert rows == {"active", "null_lc"}


@pytest.mark.offline
def test_lifecycle_not_in_column_prefix() -> None:
    """column_prefix kwarg produces qualified column name."""
    pred = lifecycle_not_in_sql_predicate(("deprecated", "draft"), "e")
    assert pred.startswith("(e.lifecycle IS NULL OR e.lifecycle NOT IN")
