"""Unit tests for panel disposition session-close detectors (thread 1206)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from agent_seat.panel_dispatch import (
    build_panel_assert_attributes,
    count_execution_evidence_uris,
)
from cortex_store.dispatch_ops._detectors.panel_disposition import (
    detect_panel_disposition_incomplete,
    detect_panel_falsifier_phase3_metric,
)
from cortex_store.dispatch_ops.ops_review_gate import _run_session_audit_or_block

_MIG_PATH = Path(__file__).parent / "migrations" / "054_assertion_attributes.py"
_spec = importlib.util.spec_from_file_location("migration_054", _MIG_PATH)
assert _spec is not None and _spec.loader is not None
_migration_054 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration_054)


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "panel_audit.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            attributes TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT,
            confidence TEXT,
            evidence_uris TEXT,
            superseded_by INTEGER,
            attributes TEXT
        );
        """
    )
    _migration_054.migrate(conn)
    conn.commit()
    return conn


def _insert_decision(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'decision', ?, NULL)",
        (entity_id, entity_id),
    )
    conn.commit()


def _insert_panel_assertion(
    conn: sqlite3.Connection,
    entity_id: str,
    attrs: dict,
    *,
    evidence_uris: list[str] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence_uris, attributes) "
        "VALUES (?, 'panel claim', 'confirmed', ?, ?)",
        (
            entity_id,
            json.dumps(evidence_uris) if evidence_uris is not None else None,
            json.dumps(attrs),
        ),
    )
    conn.commit()


def test_count_execution_evidence_uris() -> None:
    assert count_execution_evidence_uris(None) == 0
    assert (
        count_execution_evidence_uris(
            ["agent-bus:1206", "execution:eb94", "execution:fe7a", "https://x"]
        )
        == 2
    )


def test_incomplete_panel_stamp_warns(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    eid = "decision:dispatch-test-incomplete"
    _insert_decision(conn, eid)
    _insert_panel_assertion(
        conn,
        eid,
        {
            "consensus_disposition": "panel",
            "material": True,
            "panel_families": ["Grok"],
            "panel_executions": {"skeptic": "e1"},
            "decisive_falsifier": "",
            "lead_adjudication_artifact": "",
        },
    )
    findings = detect_panel_disposition_incomplete(conn, eid)
    assert any(f["kind"] == "panel_disposition_incomplete" for f in findings)


def test_complete_panel_stamp_no_warn_when_execution_uris_ok(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    attrs = build_panel_assert_attributes(
        panel_executions={"skeptic": "eb94f022", "reviewer": "fe7abdb4"},
        decisive_falsifier="missing lead artifact fraction",
        lead_adjudication_artifact="cortex:notes/system/threads/1206-lead.md",
        member_models={"skeptic": "xai/grok-4.3", "reviewer": "openai/gpt-5.5"},
    )
    eid = "decision:dispatch-test-complete"
    _insert_decision(conn, eid)
    _insert_panel_assertion(
        conn,
        eid,
        attrs,
        evidence_uris=[
            "agent-bus:1206",
            "execution:eb94f022",
            "execution:fe7abdb4",
        ],
    )
    findings = detect_panel_disposition_incomplete(conn, eid)
    assert findings == []


def test_phase3_metric_emits_when_n_ge_20(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    for i in range(20):
        eid = f"decision:panel-cohort-{i}"
        _insert_decision(conn, eid)
        _insert_panel_assertion(
            conn,
            eid,
            {
                "consensus_disposition": "panel",
                "material": True,
                "panel_families": ["Grok", "GPT"],
                "panel_executions": {"skeptic": "a", "reviewer": "b"},
                "decisive_falsifier": "falsifier text",
                "lead_adjudication_artifact": "" if i < 5 else "cortex:notes/lead.md",
            },
        )
    findings = detect_panel_falsifier_phase3_metric(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == "panel_falsifier_phase3_metric"
    assert "5/20" in findings[0]["detail"]
    assert findings[0]["severity"] == "info"


def test_phase3_metric_suppressed_when_n_lt_20(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    for i in range(19):
        eid = f"decision:panel-small-{i}"
        _insert_decision(conn, eid)
        _insert_panel_assertion(
            conn,
            eid,
            {
                "consensus_disposition": "panel",
                "material": True,
                "lead_adjudication_artifact": "",
            },
        )
    assert detect_panel_falsifier_phase3_metric(conn) == []


@pytest.fixture()
def _patch_cortex_conn(monkeypatch: pytest.MonkeyPatch):
    def _apply(conn: sqlite3.Connection) -> None:
        class _Ctx:
            def __enter__(self) -> sqlite3.Connection:
                return conn

            def __exit__(self, *exc: object) -> bool:
                return False

        monkeypatch.setattr(
            "cortex_store.dispatch_ops.ops_audit_detectors.cortex_conn",
            lambda: _Ctx(),
        )

    return _apply


def test_session_close_gate_surfaces_panel_incomplete(
    tmp_path: Path, _patch_cortex_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-close gate returns audit_findings for omitted panel stamp in entity_ids."""
    conn = _make_conn(tmp_path)
    eid = "decision:session-close-panel-smoke"
    _insert_decision(conn, eid)
    _insert_panel_assertion(
        conn,
        eid,
        {
            "consensus_disposition": "panel",
            "material": True,
            "panel_families": ["Grok"],
            "panel_executions": {"skeptic": "e1"},
            "decisive_falsifier": "",
            "lead_adjudication_artifact": "",
        },
    )
    _patch_cortex_conn(conn)
    monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "warn")
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_review_gate._PRE_CLOSE_GATE_KINDS",
        ["panel_disposition_incomplete"],
    )

    out = _run_session_audit_or_block(
        session_id="cursor-2026-06-02-panel-smoke",
        agent="cursor",
        entity_ids=[eid],
        defer_gaps=None,
    )

    assert "warning" in out
    kinds = {f["kind"] for f in out["warning"]["audit_findings"]}
    assert "panel_disposition_incomplete" in kinds
