"""Tests for hybrid Phase-2 trait backfill (scope C)."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.status_trait_backfill import (
    planned_trait_updates,
    run_hybrid_trait_backfill,
    run_scoped_confidence_band_backfill,
    run_scoped_lifecycle_active_backfill,
)

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "050_status_trait_normalization_phase0.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_050_status_trait_normalization_phase0", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_050 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_050)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            status TEXT,
            workflow_state TEXT,
            lifecycle TEXT,
            confidence_band TEXT,
            adoption TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    migration_050.migrate(c)
    return c


def test_planned_decision_provisional_maps_adoption_and_band() -> None:
    updates = planned_trait_updates(
        "decision",
        "provisional",
        confidence_band=None,
        lifecycle=None,
        adoption=None,
    )
    assert updates == {"confidence_band": "provisional", "adoption": "proposed"}


def test_planned_decision_confirmed_maps_adopted() -> None:
    updates = planned_trait_updates(
        "decision",
        "confirmed",
        confidence_band=None,
        lifecycle=None,
        adoption=None,
    )
    assert updates == {"confidence_band": "confirmed", "adoption": "adopted"}


def test_planned_deprecated_lifecycle_only() -> None:
    updates = planned_trait_updates(
        "project",
        "deprecated",
        confidence_band=None,
        lifecycle=None,
        adoption=None,
    )
    assert updates == {"lifecycle": "deprecated"}


def test_idempotent_skips_when_traits_present(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status, confidence_band, adoption) "
        "VALUES ('decision:d', 'decision', 'D', 'confirmed', 'confirmed', 'adopted')"
    )
    counts = run_hybrid_trait_backfill(
        conn, types=frozenset({"decision"}), dry_run=False
    )
    assert counts.entities_touched == 0


def test_backfill_does_not_mutate_status(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status) "
        "VALUES ('agent_skill:s', 'agent_skill', 'S', 'confirmed')"
    )
    run_hybrid_trait_backfill(conn, types=frozenset({"agent_skill"}), dry_run=False)
    row = conn.execute(
        "SELECT status, confidence_band, lifecycle, adoption FROM entities "
        "WHERE id = 'agent_skill:s'"
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["confidence_band"] == "confirmed"
    assert row["lifecycle"] is None
    assert row["adoption"] is None


def test_dry_run_no_writes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status) "
        "VALUES ('todo:t', 'todo', 'T', 'unsubstantiated')"
    )
    counts = run_hybrid_trait_backfill(conn, types=frozenset({"todo"}), dry_run=True)
    assert counts.entities_touched == 1
    assert counts.confidence_band == 1
    row = conn.execute(
        "SELECT confidence_band FROM entities WHERE id = 'todo:t'"
    ).fetchone()
    assert row["confidence_band"] is None


def test_predicate_equivalence_confirmed_maps_adoption_all_types() -> None:
    from cortex_store.status_trait_backfill import planned_predicate_equivalence_updates

    updates = planned_predicate_equivalence_updates(
        "transcript",
        "confirmed",
        confidence_band=None,
        lifecycle=None,
        adoption=None,
    )
    assert updates == {"confidence_band": "confirmed", "adoption": "adopted"}


def test_predicate_equivalence_decision_deprecated_lifecycle_and_adoption() -> None:
    from cortex_store.status_trait_backfill import planned_predicate_equivalence_updates

    updates = planned_predicate_equivalence_updates(
        "decision",
        "deprecated",
        confidence_band=None,
        lifecycle=None,
        adoption=None,
    )
    assert updates == {"lifecycle": "deprecated", "adoption": "superseded"}


def test_predicate_equivalence_backfill_all_types(conn: sqlite3.Connection) -> None:
    from cortex_store.status_trait_backfill import (
        run_predicate_equivalence_trait_backfill,
    )

    conn.execute(
        "INSERT INTO entities (id, type, name, status) "
        "VALUES ('ai_agent:a', 'ai_agent', 'A', 'deprecated')"
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status) "
        "VALUES ('transcript:t', 'transcript', 'T', 'confirmed')"
    )
    counts = run_predicate_equivalence_trait_backfill(conn, dry_run=False)
    assert counts.lifecycle >= 1
    assert counts.adoption >= 1
    assert counts.confidence_band >= 1
    agent = conn.execute(
        "SELECT lifecycle FROM entities WHERE id = 'ai_agent:a'"
    ).fetchone()
    assert agent["lifecycle"] == "deprecated"
    tx = conn.execute(
        "SELECT confidence_band, adoption FROM entities WHERE id = 'transcript:t'"
    ).fetchone()
    assert tx["confidence_band"] == "confirmed"
    assert tx["adoption"] == "adopted"


def test_scoped_lifecycle_backfill_skips_staged_assertion_entity(
    conn: sqlite3.Connection,
) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            review_status TEXT,
            superseded_by INTEGER
        );
        INSERT INTO entities (id, type, name, lifecycle, confidence_band)
        VALUES ('todo:live', 'todo', 'L', NULL, 'unsubstantiated'),
               ('todo:staged', 'todo', 'S', NULL, 'provisional');
        INSERT INTO assertions (id, entity_id, review_status, superseded_by)
        VALUES (1, 'todo:staged', 'staged', NULL);
        """
    )
    counts = run_scoped_lifecycle_active_backfill(conn, dry_run=False)
    assert counts.entities_touched == 1
    assert counts.lifecycle == 1
    live = conn.execute(
        "SELECT lifecycle FROM entities WHERE id = 'todo:live'"
    ).fetchone()
    staged = conn.execute(
        "SELECT lifecycle FROM entities WHERE id = 'todo:staged'"
    ).fetchone()
    assert live["lifecycle"] == "active"
    assert staged["lifecycle"] is None


def test_scoped_confidence_band_backfill_uses_type_defaults(
    conn: sqlite3.Connection,
) -> None:
    conn.executescript(
        """
        INSERT INTO entities (id, type, name, lifecycle, confidence_band)
        VALUES ('todo:t', 'todo', 'T', 'active', NULL),
               ('decision:d', 'decision', 'D', 'active', NULL),
               ('transcript:x', 'transcript', 'X', NULL, NULL);
        """
    )
    counts = run_scoped_confidence_band_backfill(conn, dry_run=False)
    assert counts.entities_touched == 3
    assert counts.confidence_band == 3
    todo = conn.execute(
        "SELECT confidence_band FROM entities WHERE id = 'todo:t'"
    ).fetchone()
    decision = conn.execute(
        "SELECT confidence_band FROM entities WHERE id = 'decision:d'"
    ).fetchone()
    transcript = conn.execute(
        "SELECT confidence_band FROM entities WHERE id = 'transcript:x'"
    ).fetchone()
    assert todo["confidence_band"] == "unsubstantiated"
    assert decision["confidence_band"] == "provisional"
    assert transcript["confidence_band"] == "confirmed"


def test_scoped_graduated_lifecycle_backfill_sets_active_with_staged_and_committed(
    conn: sqlite3.Connection,
) -> None:
    from cortex_store.status_trait_backfill import (
        count_graduated_null_lifecycle,
        run_scoped_graduated_lifecycle_backfill,
    )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            review_status TEXT,
            superseded_by INTEGER
        );
        INSERT INTO entities (id, type, name, lifecycle, confidence_band)
        VALUES ('todo:grad', 'todo', 'G', NULL, 'unsubstantiated');
        INSERT INTO assertions (id, entity_id, review_status, superseded_by)
        VALUES (1, 'todo:grad', 'staged', NULL),
               (2, 'todo:grad', 'committed', NULL);
        """
    )
    assert count_graduated_null_lifecycle(conn) == 1
    counts = run_scoped_graduated_lifecycle_backfill(conn, dry_run=False)
    assert counts.entities_touched == 1
    assert counts.lifecycle == 1
    row = conn.execute(
        "SELECT lifecycle FROM entities WHERE id = 'todo:grad'"
    ).fetchone()
    assert row["lifecycle"] == "active"
    assert count_graduated_null_lifecycle(conn) == 0
