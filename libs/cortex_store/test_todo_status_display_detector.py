"""Axis-aware done + unsubstantiated-band detector tests."""

from __future__ import annotations

import sqlite3

from cortex_store.dispatch_ops._detectors.todo import (
    detect_done_entity_unsubstantiated_band_mismatch,
)


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            workflow_state TEXT,
            confidence_band TEXT,
            lifecycle TEXT
        );
        CREATE TABLE type_confidence_fields (
            entity_type TEXT PRIMARY KEY,
            confidence_field TEXT NOT NULL
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT NOT NULL,
            confidence TEXT,
            superseded_by INTEGER
        );
        INSERT INTO type_confidence_fields VALUES ('todo', 'workflow_state');
        INSERT INTO type_confidence_fields VALUES ('decision', 'confidence_band');
        """
    )
    return conn


def test_workflow_state_axis_done_todo_not_flagged() -> None:
    conn = _seed_conn()
    conn.execute(
        "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
        ("todo:done-with-assertions", "todo", "Done todo", "done", "unsubstantiated", "active"),
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, confidence) VALUES (?, ?)",
        ("todo:done-with-assertions", "confirmed"),
    )
    findings = detect_done_entity_unsubstantiated_band_mismatch(conn)
    assert findings == []


def test_band_axis_done_with_assertions_is_flagged() -> None:
    conn = _seed_conn()
    conn.execute(
        "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
        ("decision:stale-band", "decision", "Stale band", "done", "unsubstantiated", "active"),
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, confidence) VALUES (?, ?)",
        ("decision:stale-band", "confirmed"),
    )
    findings = detect_done_entity_unsubstantiated_band_mismatch(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == "done_entity_unsubstantiated_band_mismatch"
    assert findings[0]["subject"] == "decision:stale-band"
