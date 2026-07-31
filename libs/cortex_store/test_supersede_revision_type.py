"""Supersede revision_type write stamp + carryover exclusion — AC-3/4."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.entity_read import get_entity_impl
from cortex_store.routes.assertions import _supersede_assertion_impl

_ASSERTIONS_DDL = """
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    confidence TEXT NOT NULL,
    confidence_score REAL,
    evidence TEXT,
    evidence_uris TEXT,
    seeded_by TEXT,
    derivation_type TEXT,
    chunk_id INTEGER,
    chunk_id_schema TEXT,
    reasoning_summary TEXT,
    is_atomic INTEGER DEFAULT 1,
    is_decontextualized INTEGER DEFAULT 1,
    observed_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    superseded_by INTEGER,
    review_status TEXT,
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    resolution_status TEXT,
    fulfillment_assertion_id INTEGER,
    quality_score REAL,
    prospective_summary TEXT,
    events_json TEXT,
    artifact_uri TEXT,
    artifact_storage TEXT DEFAULT 'inline',
    entrenchment_score REAL,
    predicate_form TEXT,
    raw_predicate_form TEXT,
    normalization_decision TEXT,
    candidate_set_fingerprint TEXT,
    normalizer_version TEXT,
    attributes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT,
    name TEXT,
    description TEXT,
    lifecycle TEXT,
    workflow_state TEXT,
    confidence_band TEXT,
    attributes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE relationship_types (type TEXT PRIMARY KEY, description TEXT);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity TEXT,
    to_entity TEXT,
    type TEXT,
    role TEXT,
    strength REAL,
    evidence TEXT,
    chunk_id TEXT,
    valid_from TEXT,
    valid_until TEXT,
    source_uri TEXT,
    session_id TEXT,
    agent TEXT,
    created_at TEXT,
    active INTEGER DEFAULT 1
);
CREATE TABLE entity_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT, agent TEXT, operation TEXT, source TEXT, session_id TEXT
);
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, agent TEXT, from_node TEXT, to_node TEXT,
    edge_type TEXT, strength REAL, edge_source TEXT, context TEXT
);
"""

_ENTITY = "test:revision-type"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ASSERTIONS_DDL)
    conn.execute(
        "INSERT INTO entities (id, type, name, description) VALUES (?, ?, ?, ?)",
        (_ENTITY, "todo", "Revision type test", "Test entity."),
    )
    conn.commit()
    return conn


def _insert(
    conn: sqlite3.Connection, *, claim: str, attributes: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, attributes)"
        " VALUES (?, ?, ?, ?, ?)",
        (_ENTITY, claim, "believed", "evidence", attributes),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def _patch_supersede(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    class _NoCloseConn:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def close(self) -> None:
            return None

    wrapper = _NoCloseConn(conn)
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.cortex_conn", lambda: wrapper
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_background",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.enrich_old_assertion_events",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.reindex_assertion_fts",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede._embed_assertion_background",
        lambda *a, **kw: None,
    )

    class _FakeImpact:
        likely_supersedes: list[int] = []
        touched_assertions: list[object] = []

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **kw: _FakeImpact(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.vector_store",
        type(
            "VS",
            (),
            {
                "is_initialized": staticmethod(lambda: False),
                "delete_assertion_embedding": staticmethod(lambda _id: None),
            },
        ),
    )


def _supersede(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    *,
    old_id: int,
    claim: str,
    revision_type: str | None = None,
) -> dict:
    _patch_supersede(monkeypatch, conn)
    payload = {
        "old_assertion_id": old_id,
        "entity_id": _ENTITY,
        "claim": claim,
        "confidence": "believed",
        "evidence": "updated evidence",
        "session_id": "sess-1",
        "agent": "test",
    }
    if revision_type is not None:
        payload["revision_type"] = revision_type
    return _supersede_assertion_impl(payload)


def test_ac3_correction_visible_in_superseded_corrections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    old_id = _insert(conn, claim="Prior belief about the system.")
    result = _supersede(
        conn,
        monkeypatch,
        old_id=old_id,
        claim="Corrected belief overturning the prior.",
        revision_type="correction",
    )
    new_id = result["new"]["id"]

    full = get_entity_impl(
        conn, entity_id=_ENTITY, include_superseded=False, source="boot"
    )
    assert all(a["id"] != old_id for a in full["assertions"])
    corrections = full.get("superseded_corrections") or []
    match = next(c for c in corrections if c["id"] == old_id)
    assert match["revision_type"] == "correction"
    assert match["superseded_by"] == new_id
    assert "Prior belief" in match["prior_claim_trunc"]
    assert "Corrected belief" in (match["new_claim_trunc"] or "")


def test_ac4_status_update_stamp_without_carryover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    first_id = _insert(conn, claim="Initial status claim.")
    first = _supersede(
        conn,
        monkeypatch,
        old_id=first_id,
        claim="Status updated claim.",
        revision_type="status_update",
    )
    assert first["new"]["attributes"]["revision_type"] == "status_update"

    second = _supersede(
        conn,
        monkeypatch,
        old_id=first["new"]["id"],
        claim="Restated without explicit revision_type.",
    )
    assert second["new"]["attributes"]["revision_type"] == "restatement"
    assert second["new"]["attributes"]["revision_type"] != "status_update"


def test_default_restatement_when_revision_type_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _make_conn()
    old_id = _insert(conn, claim="Claim to restate.")
    result = _supersede(
        conn,
        monkeypatch,
        old_id=old_id,
        claim="Restated claim.",
    )
    row = conn.execute(
        "SELECT attributes FROM assertions WHERE id = ?", (result["new"]["id"],)
    ).fetchone()
    attrs = json.loads(row["attributes"])
    assert attrs["revision_type"] == "restatement"
