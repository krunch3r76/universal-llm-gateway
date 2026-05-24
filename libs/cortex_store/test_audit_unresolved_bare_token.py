"""v1.3.1 Path 2 detector — finding kind unresolved_bare_token_in_predicate_form."""

from __future__ import annotations

import sqlite3

from cortex_store.dispatch_ops._detectors.predicate_form import (
    detect_unresolved_bare_token_in_predicate_form,
)


def _setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT NOT NULL,
            raw_predicate_form TEXT,
            normalization_decision TEXT,
            candidate_set_fingerprint TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            superseded_by INTEGER
        );
        """
    )
    return conn


def test_detector_skips_null_ledger_rows() -> None:
    """Pre-v1.3.1 rows (NULL ledger) must not be flagged — work-order §1 contract."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO assertions (id, entity_id, normalization_decision) VALUES (1, 'person:x', NULL)"
    )
    findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert findings == [], "NULL-ledger rows must be skipped"


def test_detector_emits_finding_for_collision_refused() -> None:
    """Positive case: a row with normalization_decision='collision_refused' produces one finding."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO assertions (id, entity_id, raw_predicate_form, normalization_decision, "
        "candidate_set_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (
            42,
            "decision:test",
            "status(fred_mansubi, ready)",
            "collision_refused",
            "abc123def456",
        ),
    )
    findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}: {findings}"
    f = findings[0]
    assert f["kind"] == "unresolved_bare_token_in_predicate_form"
    assert f["severity"] == "warning"  # v1.3.1 is shadow-mode, warning-grade
    assert "42" in f["subject"]  # subject references the assertion id
    assert "collision_refused" in f["detail"]


def test_detector_emits_finding_for_alias_collision_refused() -> None:
    """alias_collision_refused (v1.4 reservation) also produces a finding when present."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO assertions (id, entity_id, raw_predicate_form, normalization_decision, "
        "candidate_set_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (
            7,
            "decision:test",
            "status(mary_mansubi, ready)",
            "alias_collision_refused",
            "deadbeefcafebabe",
        ),
    )
    findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == "unresolved_bare_token_in_predicate_form"


def test_detector_skips_superseded_rows() -> None:
    """Refused rows that have been superseded are no longer active — skip per active-only contract."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO assertions (id, entity_id, raw_predicate_form, normalization_decision, "
        "candidate_set_fingerprint, superseded_by) VALUES (?, ?, ?, ?, ?, ?)",
        (
            99,
            "decision:test",
            "status(fred_mansubi, ready)",
            "collision_refused",
            "abc",
            100,
        ),
    )
    findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert findings == [], "superseded rows must be skipped"


def test_detector_subject_filter() -> None:
    """Optional `subject` arg filters by entity_id."""
    conn = _setup_conn()
    conn.execute(
        "INSERT INTO assertions (id, entity_id, raw_predicate_form, normalization_decision, "
        "candidate_set_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (1, "person:a", "raw_a", "collision_refused", "fp1"),
    )
    conn.execute(
        "INSERT INTO assertions (id, entity_id, raw_predicate_form, normalization_decision, "
        "candidate_set_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (2, "person:b", "raw_b", "collision_refused", "fp2"),
    )
    all_findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert len(all_findings) == 2
    a_findings = detect_unresolved_bare_token_in_predicate_form(
        conn, subject="person:a"
    )
    assert len(a_findings) == 1
    assert "1" in a_findings[0]["subject"]
