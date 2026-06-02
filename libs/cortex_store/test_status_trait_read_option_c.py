"""Option C read cutover — synthesized display + trait keys."""

from __future__ import annotations

import sqlite3

from cortex_store.card_adapters import DecisionAdapter, PersonAdapter
from cortex_store.status_trait_read import (
    apply_option_c_read_projection,
    card_status_summary_option_c,
    synthesize_status_display,
)


def test_synthesize_person_confirmed_active() -> None:
    row = {
        "status": "confirmed",
        "confidence_band": "confirmed",
        "lifecycle": "active",
    }
    assert synthesize_status_display(row) == "confirmed · active"
    summary = card_status_summary_option_c(row)
    assert summary["status"] == "confirmed · active"
    assert summary["confidence_band"] == "confirmed"
    assert summary["lifecycle"] == "active"
    assert "adoption" not in summary


def test_synthesize_decision_provisional_proposed() -> None:
    row = {
        "status": "provisional",
        "confidence_band": "provisional",
        "adoption": "proposed",
        "type": "decision",
    }
    assert synthesize_status_display(row) == "provisional · proposed"
    projected = apply_option_c_read_projection(row)
    assert projected["status"] == "provisional · proposed"
    assert projected["adoption"] == "proposed"


def test_synthesize_lifecycle_trait_only_deprecated() -> None:
    row = {"lifecycle": "deprecated"}
    assert synthesize_status_display(row) == "deprecated"
    projected = apply_option_c_read_projection(row)
    assert projected["lifecycle"] == "deprecated"
    assert projected["status"] == "deprecated"


def test_base_adapter_status_summary_option_c_keys() -> None:
    entity = {
        "status": "confirmed",
        "confidence_band": "confirmed",
        "lifecycle": "active",
        "workflow_state": None,
        "updated_at": "2026-06-02T00:00:00Z",
    }
    summary = PersonAdapter().status_summary(entity)
    assert summary is not None
    assert summary["status"] == "confirmed · active"
    assert summary["confidence_band"] == "confirmed"
    assert summary["lifecycle"] == "active"
    assert summary["updated_at"] == "2026-06-02T00:00:00Z"


def test_decision_adapter_inherits_option_c_display() -> None:
    entity = {
        "status": "confirmed",
        "confidence_band": "confirmed",
        "adoption": "adopted",
        "updated_at": "2026-06-02T00:00:00Z",
        "workflow_state": "accepted",
    }
    summary = DecisionAdapter().status_summary(entity)
    assert summary is not None
    assert summary["status"] == "confirmed · adopted"
    assert summary["adoption"] == "adopted"


def test_entity_has_trait_columns_pragma_safe() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, status TEXT, lifecycle TEXT, "
        "confidence_band TEXT)"
    )
    from cortex_store.status_trait_read import entity_has_trait_columns

    assert entity_has_trait_columns(conn) is True
    conn.execute("DROP TABLE entities")
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, status TEXT)")
    assert entity_has_trait_columns(conn) is False
