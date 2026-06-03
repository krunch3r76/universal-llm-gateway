"""Regression: entity_update honors Option-C trait writes (a:13001 / thread 1226).

claude-web hit a silent drop: ``entity_update(confidence_band='confirmed')`` and
``(adoption='adopted')`` each returned ``{error: 'No fields to update'}`` and the
column never advanced, even though the MCP descriptor advertises these as the
trait write surface. Root cause: the dispatch-layer whitelist ``_ENTITY_MUTABLE``
omitted the three explicit trait columns, so they were filtered out before
reaching ``update_entity_impl`` (which builds its SQL directly from the updates
dict and could persist them). This pins the fix.
"""

from __future__ import annotations

import sqlite3

from cortex_store.dispatch_ops._shared import _ENTITY_MUTABLE
from cortex_store.dispatch_ops.ops_entities import _validate_trait_updates
from cortex_store.entity_crud import update_entity_impl


def _conn() -> sqlite3.Connection:
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
            confidence_band TEXT,
            lifecycle TEXT,
            adoption TEXT,
            confidence_score REAL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY, entity_id TEXT, claim TEXT, confidence TEXT,
            confidence_score REAL, evidence TEXT, evidence_uris TEXT, seeded_by TEXT,
            derivation_type TEXT, chunk_id TEXT, chunk_id_schema TEXT,
            reasoning_summary TEXT, is_atomic INTEGER, is_decontextualized INTEGER,
            observed_at TEXT, valid_from TEXT, valid_until TEXT, superseded_by INTEGER,
            review_status TEXT, reviewer TEXT, reviewed_at TEXT, review_notes TEXT,
            resolution_status TEXT, fulfillment_assertion_id INTEGER, quality_score REAL,
            prospective_summary TEXT, events_json TEXT, artifact_uri TEXT,
            artifact_storage TEXT, entrenchment_score REAL, predicate_form TEXT,
            created_at TEXT, raw_predicate_form TEXT, normalization_decision TEXT,
            candidate_set_fingerprint TEXT, normalizer_version TEXT, attributes TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, confidence_band, adoption, created_at) "
        "VALUES ('decision:x', 'decision', 'X', 'provisional', 'proposed', "
        "'2026-06-03T00:00:00Z')",
    )
    conn.commit()
    return conn


def test_trait_columns_are_mutable() -> None:
    assert {"confidence_band", "lifecycle", "adoption"} <= _ENTITY_MUTABLE


def test_validate_trait_updates_accepts_valid() -> None:
    assert _validate_trait_updates({"confidence_band": "confirmed"}) is None
    assert _validate_trait_updates({"adoption": "adopted"}) is None
    assert _validate_trait_updates({"lifecycle": "deprecated"}) is None
    assert _validate_trait_updates({"name": "no traits here"}) is None


def test_validate_trait_updates_rejects_out_of_vocab() -> None:
    err = _validate_trait_updates({"confidence_band": "totally-confirmed"})
    assert err is not None and "Invalid confidence_band" in err["error"]
    err = _validate_trait_updates({"adoption": "maybe"})
    assert err is not None and "Invalid adoption" in err["error"]


def test_confidence_band_promotion_persists() -> None:
    conn = _conn()
    update_entity_impl(
        conn, entity_id="decision:x", updates={"confidence_band": "confirmed"}
    )
    row = conn.execute(
        "SELECT confidence_band FROM entities WHERE id='decision:x'"
    ).fetchone()
    assert row["confidence_band"] == "confirmed"


def test_adoption_write_persists() -> None:
    conn = _conn()
    update_entity_impl(conn, entity_id="decision:x", updates={"adoption": "adopted"})
    row = conn.execute("SELECT adoption FROM entities WHERE id='decision:x'").fetchone()
    assert row["adoption"] == "adopted"
