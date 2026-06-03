"""Regression: partial attributes update merges into the prior blob.

Exposed by todo_close_sidecar (todo:todo-closure-summary-sidecar): setting
``attributes.closure_summary_uri`` on a todo that already carried
``priority``/``domain`` previously clobbered the whole attributes column,
because ``update_entity_impl`` computed a merged dict but the SQL write
iterated the raw ``updates``. This pins the merge semantics.
"""

from __future__ import annotations

import json
import sqlite3

from cortex_store.dispatch_ops._todo_closure_sidecar import render_closure_markdown
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
            candidate_set_fingerprint TEXT, normalizer_version TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes, created_at) "
        "VALUES ('todo:x', 'todo', 'X', ?, '2026-06-03T00:00:00Z')",
        (json.dumps({"priority": "medium", "domain": "cortex"}),),
    )
    conn.commit()
    return conn


def test_partial_attributes_update_preserves_prior_keys() -> None:
    conn = _conn()
    update_entity_impl(
        conn,
        entity_id="todo:x",
        updates={"attributes": {"closure_summary_uri": "cortex://notes/x-closure.md"}},
    )
    row = conn.execute("SELECT attributes FROM entities WHERE id='todo:x'").fetchone()
    attrs = json.loads(row["attributes"])
    assert attrs == {
        "priority": "medium",
        "domain": "cortex",
        "closure_summary_uri": "cortex://notes/x-closure.md",
    }


def test_attributes_update_overwrites_same_key() -> None:
    conn = _conn()
    update_entity_impl(
        conn, entity_id="todo:x", updates={"attributes": {"priority": "high"}}
    )
    row = conn.execute("SELECT attributes FROM entities WHERE id='todo:x'").fetchone()
    attrs = json.loads(row["attributes"])
    assert attrs == {"priority": "high", "domain": "cortex"}


def test_render_closure_markdown_shape() -> None:
    md = render_closure_markdown(
        todo_id="todo:x",
        summary="Did the thing.",
        evidence="Closure of todo:x.",
        reasoning_summary="Because.",
        references=[{"target": "todo:y", "role": "extends"}],
        agent="cursor",
        session_id="cursor-2026-06-03-0010",
        closed_at="2026-06-03T00:10:00Z",
    )
    assert md.startswith("# Closure — todo:x")
    assert "## Summary" in md
    assert "Did the thing." in md
    assert "## Reasoning" in md
    assert "## References" in md
    assert "`todo:y` (extends)" in md
    assert md.endswith("\n")
