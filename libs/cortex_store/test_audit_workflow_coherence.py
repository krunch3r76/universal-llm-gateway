"""Detector tests — decision_workflow_state_incoherent + deprecated_not_terminal (thread 1116)."""

from __future__ import annotations

import sqlite3

from cortex_store.dispatch_ops._detectors.workflow_coherence import (
    detect_decision_deprecated_not_terminal,
    detect_decision_workflow_state_incoherent,
)


def _setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT,
            workflow_state TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            from_entity TEXT,
            to_entity TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE workflow_schemas (
            entity_type TEXT PRIMARY KEY,
            enum_values TEXT,
            initial_state TEXT,
            terminal_states TEXT
        );
        INSERT INTO workflow_schemas VALUES (
            'decision',
            '["proposed","accepted","implemented","superseded","reverted"]',
            'proposed',
            '["implemented","superseded","reverted"]'
        );
        """
    )
    return conn


def test_flags_confirmed_null_and_proposed() -> None:
    conn = _setup_conn()
    conn.executescript(
        """
        INSERT INTO entities VALUES ('decision:null-legacy', 'decision', 'confirmed', NULL);
        INSERT INTO entities VALUES ('decision:proposed-live', 'decision', 'confirmed', 'proposed');
        """
    )
    findings = detect_decision_workflow_state_incoherent(conn)
    assert len(findings) == 2
    assert {f["subject"] for f in findings} == {
        "decision:null-legacy",
        "decision:proposed-live",
    }
    assert all(f["kind"] == "decision_workflow_state_incoherent" for f in findings)
    assert all(f["severity"] == "warning" for f in findings)


def test_skips_provisional_at_proposed() -> None:
    """A provisional (not-yet-adopted) decision at proposed is coherent — no false positive."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO entities VALUES ('decision:prov', 'decision', 'provisional', 'proposed')"
    )
    assert detect_decision_workflow_state_incoherent(conn) == []


def test_skips_confirmed_accepted_and_implemented() -> None:
    conn = _setup_conn()
    conn.executescript(
        """
        INSERT INTO entities VALUES ('decision:a', 'decision', 'confirmed', 'accepted');
        INSERT INTO entities VALUES ('decision:i', 'decision', 'confirmed', 'implemented');
        """
    )
    assert detect_decision_workflow_state_incoherent(conn) == []


def test_suggests_superseded_when_supersedes_edge_present() -> None:
    conn = _setup_conn()
    conn.executescript(
        """
        INSERT INTO entities VALUES ('decision:old', 'decision', 'confirmed', 'proposed');
        INSERT INTO entities VALUES ('decision:new', 'decision', 'confirmed', 'accepted');
        INSERT INTO relationships (type, from_entity, to_entity, active)
            VALUES ('supersedes', 'decision:new', 'decision:old', 1);
        """
    )
    findings = detect_decision_workflow_state_incoherent(conn)
    assert len(findings) == 1
    assert "'superseded'" in findings[0]["detail"]


def test_subject_filter() -> None:
    conn = _setup_conn()
    conn.executescript(
        """
        INSERT INTO entities VALUES ('decision:x', 'decision', 'confirmed', 'proposed');
        INSERT INTO entities VALUES ('decision:y', 'decision', 'confirmed', NULL);
        """
    )
    assert len(detect_decision_workflow_state_incoherent(conn)) == 2
    only_x = detect_decision_workflow_state_incoherent(conn, subject="decision:x")
    assert len(only_x) == 1
    assert only_x[0]["subject"] == "decision:x"


def test_deprecated_not_terminal_info() -> None:
    conn = _setup_conn()
    conn.executescript(
        """
        INSERT INTO entities VALUES ('decision:dep-null', 'decision', 'deprecated', NULL);
        INSERT INTO entities VALUES ('decision:dep-prop', 'decision', 'deprecated', 'proposed');
        INSERT INTO entities VALUES ('decision:dep-sup', 'decision', 'deprecated', 'superseded');
        """
    )
    findings = detect_decision_deprecated_not_terminal(conn)
    subjects = {f["subject"] for f in findings}
    assert subjects == {"decision:dep-null", "decision:dep-prop"}
    assert all(f["severity"] == "info" for f in findings)
