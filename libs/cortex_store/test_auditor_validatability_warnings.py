"""Regression tests for auditor-validatability validation_warnings (Checks 1–5).

Checks 1–3: unit tests for check_confirmed_validatability() — no DB required.
Checks 4–5: integration tests using a minimal in-memory SQLite schema, matching
the existing test_session_close_handoff.py fixture pattern.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cortex_store.assertion_quality import check_confirmed_validatability
from cortex_store.dispatch_ops.ops_audit_detectors import (
    detect_confirmed_attribute_no_assertion,
    detect_confirmed_entity_no_assertions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _w(warnings: list[dict], field: str) -> list[dict]:
    """Filter warnings by field."""
    return [w for w in warnings if w.get("field") == field]


# ---------------------------------------------------------------------------
# Check 1 — confirmed + no evidence_uris
# ---------------------------------------------------------------------------


def test_check1_confirmed_no_evidence_uris_warns() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=None,
        derivation_type="direct_observation",
        claim="Some fact.",
    )
    ev = _w(w, "evidence_uris")
    assert ev, "expected evidence_uris warning"
    assert "auditor" in ev[0]["message"]


def test_check1_confirmed_empty_evidence_uris_warns() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=[],
        derivation_type="direct_observation",
        claim="Some fact.",
    )
    ev = _w(w, "evidence_uris")
    assert ev, "expected evidence_uris warning for empty list"


def test_check1_confirmed_with_evidence_uris_no_warn() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=["https://example.org/doc"],
        derivation_type="direct_observation",
        claim='"Exact verbatim quote from the source document here."',
    )
    assert not _w(w, "evidence_uris")


def test_check1_non_confirmed_skipped() -> None:
    w = check_confirmed_validatability(
        confidence="believed",
        evidence_uris=None,
        derivation_type="inference",
        claim="Some fact.",
    )
    assert w == [], "non-confirmed should produce no warnings"


def test_check1_suppress_via_acknowledge_audit_gaps() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=None,
        derivation_type="direct_observation",
        claim="Some fact.",
        acknowledge_audit_gaps=["no_evidence_uris"],
    )
    assert not _w(w, "evidence_uris"), "suppressed via acknowledge_audit_gaps"


# ---------------------------------------------------------------------------
# Check 2 — confirmed + inference
# ---------------------------------------------------------------------------


def test_check2_confirmed_with_inference_warns() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=["https://example.org"],
        derivation_type="inference",
        claim="Some synthesised claim.",
    )
    dt = _w(w, "derivation_type")
    assert dt, "expected derivation_type warning"
    assert "inference" in dt[0]["message"]


def test_check2_confirmed_with_direct_observation_no_warn() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=["https://example.org"],
        derivation_type="direct_observation",
        claim='"Source text verbatim quote present here."',
    )
    assert not _w(w, "derivation_type")


def test_check2_suppress_via_acknowledge_audit_gaps() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        evidence_uris=["https://example.org"],
        derivation_type="inference",
        claim="Synthesised claim.",
        acknowledge_audit_gaps=["inference_confirmed"],
    )
    assert not _w(w, "derivation_type"), "suppressed via acknowledge_audit_gaps"


# ---------------------------------------------------------------------------
# Check 3 — confirmed + verbatim-expected type + evidence + no quoted string
# ---------------------------------------------------------------------------


def test_check3_confirmed_no_verbatim_warns() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        derivation_type="direct_observation",
        # no quoted string ≥15 chars
        claim="Effective date is 2025-01-01 per the source.",
        evidence_uris=["https://example.org"],
    )
    cl = _w(w, "claim")
    assert cl, "expected claim warning"
    assert "verbatim" in cl[0]["message"]


def test_check3_confirmed_with_verbatim_no_warn() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        derivation_type="direct_observation",
        claim=(
            "Footer verbatim: '(Amended by Stats. 2024, Ch. 922, Sec. 7. "
            "(AB 3134) Effective January 1, 2025.)'"
        ),
        evidence_uris=["https://example.org"],
    )
    assert not _w(w, "claim"), "verbatim present — no warning expected"


def test_check3_no_evidence_uris_skips_verbatim_check() -> None:
    # When evidence_uris absent, Check 1 fires but Check 3 should NOT
    # (no point checking for verbatim if there is nothing to verify against).
    w = check_confirmed_validatability(
        confidence="confirmed",
        derivation_type="direct_observation",
        claim="Effective date is 2025-01-01 per the source.",
        evidence_uris=None,
    )
    assert not _w(w, "claim"), "Check 3 should not fire without evidence_uris"


def test_check3_inference_derivation_skips_verbatim_check() -> None:
    # inference is not in _VERBATIM_EXPECTED_TYPES so Check 3 should not fire.
    w = check_confirmed_validatability(
        confidence="confirmed",
        derivation_type="inference",
        claim="Some inferred claim without quote.",
        evidence_uris=["https://example.org"],
        acknowledge_audit_gaps=["inference_confirmed"],
    )
    assert not _w(w, "claim"), "inference not in verbatim-expected types"


def test_check3_suppress_via_acknowledge_audit_gaps() -> None:
    w = check_confirmed_validatability(
        confidence="confirmed",
        derivation_type="direct_observation",
        claim="Structural claim without verbatim.",
        evidence_uris=["https://example.org"],
        acknowledge_audit_gaps=["no_verbatim"],
    )
    assert not _w(w, "claim"), "suppressed via acknowledge_audit_gaps"


# ---------------------------------------------------------------------------
# Checks 4 & 5 — DB integration tests
# ---------------------------------------------------------------------------


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test_cortex.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            attributes TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT,
            confidence TEXT,
            superseded_by INTEGER
        );
        """
    )
    conn.commit()
    return conn


def _insert_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    entity_type: str = "legal_source",
    status: str = "confirmed",
    attributes: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, status, attributes) VALUES (?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            entity_id,
            status,
            json.dumps(attributes) if attributes else None,
        ),
    )
    conn.commit()


def _insert_assertion(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    confidence: str = "confirmed",
    superseded_by: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, superseded_by) "
        "VALUES (?, ?, ?, ?)",
        (entity_id, claim, confidence, superseded_by),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# --- Check 4 ---


def test_check4_confirmed_entity_no_assertions_warns(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-4a", status="confirmed")
    # No assertions at all.
    findings = detect_confirmed_entity_no_assertions(
        conn, subject="legal_source:test-4a"
    )
    assert any(f["kind"] == "confirmed_entity_no_assertions" for f in findings)


def test_check4_confirmed_entity_with_confirmed_assertion_no_warn(
    tmp_path: Path,
) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-4b", status="confirmed")
    _insert_assertion(
        conn, "legal_source:test-4b", "Confirmed fact.", confidence="confirmed"
    )
    findings = detect_confirmed_entity_no_assertions(
        conn, subject="legal_source:test-4b"
    )
    assert not findings


def test_check4_entity_with_only_believed_assertion_warns(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-4c", status="confirmed")
    _insert_assertion(
        conn, "legal_source:test-4c", "Believed fact.", confidence="believed"
    )
    findings = detect_confirmed_entity_no_assertions(
        conn, subject="legal_source:test-4c"
    )
    assert any(f["kind"] == "confirmed_entity_no_assertions" for f in findings)


def test_check4_superseded_confirmed_assertion_still_warns(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-4d", status="confirmed")
    old_id = _insert_assertion(
        conn, "legal_source:test-4d", "Old confirmed fact.", confidence="confirmed"
    )
    # Supersede the only confirmed assertion.
    _insert_assertion(
        conn,
        "legal_source:test-4d",
        "New fact.",
        confidence="believed",
        superseded_by=None,
    )
    conn.execute("UPDATE assertions SET superseded_by = 99 WHERE id = ?", (old_id,))
    conn.commit()
    findings = detect_confirmed_entity_no_assertions(
        conn, subject="legal_source:test-4d"
    )
    assert any(f["kind"] == "confirmed_entity_no_assertions" for f in findings)


def test_check4_provisional_entity_not_flagged(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-4e", status="provisional")
    findings = detect_confirmed_entity_no_assertions(
        conn, subject="legal_source:test-4e"
    )
    assert not findings, "provisional entity should not be flagged by Check 4"


# --- Check 5 ---


def test_check5_confirmed_attribute_no_assertion_warns(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(
        conn,
        "legal_source:test-5a",
        status="confirmed",
        attributes={"original_effective_date": "2021-02-16"},
    )
    # No assertions at all — Check 5 should fire.
    findings = detect_confirmed_attribute_no_assertion(
        conn, subject="legal_source:test-5a"
    )
    assert any(
        f["kind"] == "confirmed_attribute_no_assertion"
        and "original_effective_date" in f["subject"]
        for f in findings
    )


def test_check5_attribute_referenced_by_assertion_no_warn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(
        conn,
        "legal_source:test-5b",
        status="confirmed",
        attributes={"original_effective_date": "2021-02-16"},
    )
    _insert_assertion(
        conn,
        "legal_source:test-5b",
        "Original effective date is 2021-02-16 per Cal. Const. Art. XIII A.",
        confidence="confirmed",
    )
    findings = detect_confirmed_attribute_no_assertion(
        conn, subject="legal_source:test-5b"
    )
    assert not findings, "attribute referenced in assertion — no warning expected"


def test_check5_attribute_value_referenced_no_warn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(
        conn,
        "legal_source:test-5c",
        status="confirmed",
        attributes={"effective_date": "2025-01-01"},
    )
    _insert_assertion(
        conn,
        "legal_source:test-5c",
        "This section became effective 2025-01-01.",
        confidence="confirmed",
    )
    findings = detect_confirmed_attribute_no_assertion(
        conn, subject="legal_source:test-5c"
    )
    assert not findings, "attribute value found in assertion text — no warning expected"


def test_check5_no_attributes_no_warn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(conn, "legal_source:test-5d", status="confirmed", attributes=None)
    findings = detect_confirmed_attribute_no_assertion(
        conn, subject="legal_source:test-5d"
    )
    assert not findings


def test_check5_provisional_entity_not_flagged(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    _insert_entity(
        conn,
        "legal_source:test-5e",
        status="provisional",
        attributes={"effective_date": "2025-01-01"},
    )
    findings = detect_confirmed_attribute_no_assertion(
        conn, subject="legal_source:test-5e"
    )
    assert not findings, "provisional entity should not be flagged by Check 5"
