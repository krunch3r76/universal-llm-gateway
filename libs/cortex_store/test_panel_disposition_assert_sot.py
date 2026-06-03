"""Tests for panel disposition detector — assertion SOT (F1/1211).

Validates that:
- Detectors read from assertion.attributes ONLY (consensus-steelman-posture §3.1)
- Entity-only panel stamps (old wrong path) produce NO findings — the blob is
  not consulted.
- Incomplete assertion-level stamps surface the right validation reasons.
- Complete assertion-level stamps produce no findings.
- Falsifier metric fires at N >= MIN_MATERIAL_PANEL_COHORT.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

# --- Load migration under test ------------------------------------------------

_MIG_PATH = Path(__file__).parent / "migrations" / "054_assertion_attributes.py"
_spec = importlib.util.spec_from_file_location("migration_054", _MIG_PATH)
assert _spec is not None and _spec.loader is not None
_migration_054 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration_054)

# --- Import detectors under test (after sys.path resolution via pytest.ini) ---

from cortex_store.dispatch_ops._detectors.panel_disposition import (  # noqa: E402
    MIN_MATERIAL_PANEL_COHORT,
    detect_panel_disposition_incomplete,
    detect_panel_falsifier_phase3_metric,
)

# --- Minimal schema -----------------------------------------------------------

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT,
    attributes TEXT
);

CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    claim TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'believed',
    evidence TEXT,
    evidence_uris TEXT,
    superseded_by INTEGER,
    attributes TEXT
);
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    _migration_054.migrate(c)
    return c


# --- Helpers ------------------------------------------------------------------


def _insert_entity(
    conn: sqlite3.Connection, entity_id: str, attrs: dict | None = None
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, attributes) VALUES (?, 'decision', ?)",
        (entity_id, json.dumps(attrs) if attrs else None),
    )


def _insert_assertion(
    conn: sqlite3.Connection,
    entity_id: str,
    attrs: dict | None = None,
    evidence_uris: list[str] | None = None,
    superseded_by: int | None = None,
    confidence: str = "believed",
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions "
        "(entity_id, confidence, evidence_uris, superseded_by, attributes) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            entity_id,
            confidence,
            json.dumps(evidence_uris) if evidence_uris is not None else None,
            superseded_by,
            json.dumps(attrs) if attrs else None,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


_COMPLETE_ATTRS = {
    "consensus_disposition": "panel",
    "material": True,
    "panel_families": ["Claude", "GPT"],
    "panel_executions": {"skeptic": "exec-1", "reviewer": "exec-2"},
    "decisive_falsifier": "15 of 309",
    "lead_adjudication_artifact": "agent-bus:1206/turn/7",
}

_COMPLETE_URIS = ["agent-bus:1206", "execution:exec-1", "execution:exec-2"]


# --- SOT isolation: entity blob is never consulted ---------------------------


def test_entity_blob_only_panel_not_detected(conn: sqlite3.Connection) -> None:
    """Old path (entity_update only) produces NO findings — blob is not SOT."""
    _insert_entity(conn, "decision:old-path", _COMPLETE_ATTRS)
    # No assertion with attributes
    _insert_assertion(
        conn, "decision:old-path", attrs=None, evidence_uris=_COMPLETE_URIS
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert findings == [], (
        "Detector must NOT consult entity.attributes; "
        "entity-only panel stamps should produce no findings"
    )


# --- Assertion SOT: incomplete stamp is flagged --------------------------------


def test_assertion_panel_missing_falsifier_flagged(conn: sqlite3.Connection) -> None:
    """Assertion with panel attrs but empty decisive_falsifier is flagged."""
    _insert_entity(conn, "decision:incomplete")
    attrs = {**_COMPLETE_ATTRS, "decisive_falsifier": ""}
    _insert_assertion(
        conn, "decision:incomplete", attrs=attrs, evidence_uris=_COMPLETE_URIS
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert len(findings) == 1
    detail = findings[0]["detail"]
    assert "decisive_falsifier" in detail
    assert "decision:incomplete" in detail


def test_assertion_panel_missing_lead_artifact_flagged(
    conn: sqlite3.Connection,
) -> None:
    """Assertion with panel attrs but no lead_adjudication_artifact is flagged."""
    _insert_entity(conn, "decision:no-artifact")
    attrs = {**_COMPLETE_ATTRS, "lead_adjudication_artifact": ""}
    _insert_assertion(
        conn, "decision:no-artifact", attrs=attrs, evidence_uris=_COMPLETE_URIS
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert len(findings) == 1
    assert "lead_adjudication_artifact" in findings[0]["detail"]


def test_assertion_panel_insufficient_execution_uris_flagged(
    conn: sqlite3.Connection,
) -> None:
    """Assertion with < MIN_EXECUTION_EVIDENCE_URIS execution: URIs is flagged."""
    _insert_entity(conn, "decision:few-uris")
    _insert_assertion(
        conn,
        "decision:few-uris",
        attrs=_COMPLETE_ATTRS,
        evidence_uris=["agent-bus:123", "execution:only-one"],
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert len(findings) == 1
    detail = findings[0]["detail"]
    assert "execution:" in detail
    assert "evidence_uris" in detail


def test_assertion_panel_no_execution_uris_flagged(conn: sqlite3.Connection) -> None:
    """Assertion with no evidence_uris at all is flagged for execution URIs."""
    _insert_entity(conn, "decision:no-uris")
    _insert_assertion(
        conn, "decision:no-uris", attrs=_COMPLETE_ATTRS, evidence_uris=None
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert len(findings) == 1


# --- Complete stamp: no findings ---------------------------------------------


def test_complete_assertion_panel_no_findings(conn: sqlite3.Connection) -> None:
    """A fully-stamped assertion-level panel produces no findings."""
    _insert_entity(conn, "decision:complete")
    _insert_assertion(
        conn, "decision:complete", attrs=_COMPLETE_ATTRS, evidence_uris=_COMPLETE_URIS
    )

    findings = detect_panel_disposition_incomplete(conn)
    assert findings == []


# --- Subject scoping ---------------------------------------------------------


def test_subject_scope_isolates_entity(conn: sqlite3.Connection) -> None:
    """subject= parameter scopes to one entity_id."""
    _insert_entity(conn, "decision:target")
    _insert_entity(conn, "decision:bystander")
    attrs_bad = {**_COMPLETE_ATTRS, "decisive_falsifier": ""}
    _insert_assertion(
        conn, "decision:target", attrs=attrs_bad, evidence_uris=_COMPLETE_URIS
    )
    _insert_assertion(
        conn, "decision:bystander", attrs=_COMPLETE_ATTRS, evidence_uris=_COMPLETE_URIS
    )

    findings = detect_panel_disposition_incomplete(conn, subject="decision:target")
    assert len(findings) == 1
    assert findings[0]["subject"] == "decision:target"

    findings_bystander = detect_panel_disposition_incomplete(
        conn, subject="decision:bystander"
    )
    assert findings_bystander == []


# --- Superseded assertion not consulted --------------------------------------


def test_superseded_assertion_not_consulted(conn: sqlite3.Connection) -> None:
    """A superseded assertion with panel attrs is ignored; the live one controls."""
    _insert_entity(conn, "decision:superseded-test")
    old_id = _insert_assertion(
        conn,
        "decision:superseded-test",
        attrs={**_COMPLETE_ATTRS, "decisive_falsifier": ""},
        evidence_uris=_COMPLETE_URIS,
    )
    # Supersede with a complete stamp
    new_id = _insert_assertion(
        conn,
        "decision:superseded-test",
        attrs=_COMPLETE_ATTRS,
        evidence_uris=_COMPLETE_URIS,
    )
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?", (new_id, old_id)
    )
    conn.commit()

    findings = detect_panel_disposition_incomplete(conn)
    assert findings == [], "Superseded assertion must not be consulted"


# --- Non-material panel not flagged ------------------------------------------


def test_non_material_panel_not_flagged(conn: sqlite3.Connection) -> None:
    """material=False excludes the assertion from the cohort."""
    _insert_entity(conn, "decision:non-material")
    attrs = {**_COMPLETE_ATTRS, "material": False, "decisive_falsifier": ""}
    _insert_assertion(conn, "decision:non-material", attrs=attrs, evidence_uris=[])

    findings = detect_panel_disposition_incomplete(conn)
    assert findings == []


# --- Falsifier metric: N < 20 returns nothing --------------------------------


def test_falsifier_metric_below_threshold(conn: sqlite3.Connection) -> None:
    """Phase-3 metric is silent when cohort < MIN_MATERIAL_PANEL_COHORT."""
    for i in range(MIN_MATERIAL_PANEL_COHORT - 1):
        eid = f"decision:panel-{i}"
        _insert_entity(conn, eid)
        attrs = {**_COMPLETE_ATTRS, "lead_adjudication_artifact": ""}
        _insert_assertion(conn, eid, attrs=attrs, evidence_uris=_COMPLETE_URIS)

    findings = detect_panel_falsifier_phase3_metric(conn)
    assert findings == []


def test_falsifier_metric_fires_at_threshold(conn: sqlite3.Connection) -> None:
    """Phase-3 metric fires when cohort >= MIN_MATERIAL_PANEL_COHORT with missing artifacts."""
    for i in range(MIN_MATERIAL_PANEL_COHORT):
        eid = f"decision:big-cohort-{i}"
        _insert_entity(conn, eid)
        attrs = {**_COMPLETE_ATTRS, "lead_adjudication_artifact": ""}
        _insert_assertion(conn, eid, attrs=attrs, evidence_uris=_COMPLETE_URIS)

    findings = detect_panel_falsifier_phase3_metric(conn)
    assert len(findings) == 1
    assert "fraction=" in findings[0]["detail"]


def test_falsifier_metric_all_complete_no_finding(conn: sqlite3.Connection) -> None:
    """Phase-3 metric is silent when all cohort members have lead_adjudication_artifact."""
    for i in range(MIN_MATERIAL_PANEL_COHORT):
        eid = f"decision:all-ok-{i}"
        _insert_entity(conn, eid)
        _insert_assertion(
            conn, eid, attrs=_COMPLETE_ATTRS, evidence_uris=_COMPLETE_URIS
        )

    findings = detect_panel_falsifier_phase3_metric(conn)
    assert findings == []


# --- Migration idempotency ---------------------------------------------------


def test_migration_054_idempotent(conn: sqlite3.Connection) -> None:
    """Replaying migration 054 is a no-op (duplicate column name caught)."""
    _migration_054.migrate(conn)  # second run
    cols = {row[1] for row in conn.execute("PRAGMA table_info(assertions)").fetchall()}
    assert "attributes" in cols
