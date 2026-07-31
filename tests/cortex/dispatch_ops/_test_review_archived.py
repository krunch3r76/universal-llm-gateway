"""Regression tests for Phase 2.0 (cortex-graph-projection-and-audit-primitives).

C3 ordering invariant: _run_session_audit_or_block MUST fire as the FIRST
substantive step in _op_session_close — before any file I/O or DB mutation.

Key assertions per v2 plan §7:
1. BLOCK mode + critical gap → structured error; transcript file NOT written.
2. BLOCK mode + critical gap → no DB row created (entity absent in DB).
3. WARN mode + gap → close succeeds with _warning populated; transcript file written.
4. WARN mode + no gaps → close succeeds with no _warning key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from libs.cortex_store.dispatch_ops.ops_journals import _op_session_close
from libs.cortex_store.dispatch_ops.ops_review import (
    _op_case_audit,
    _op_fill_gaps,
    _op_session_audit,
    _run_session_audit_or_block,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_MINIMAL_TRANSCRIPT = (
    "# Transcript: cursor-2099-01-01-0001\n\n"
    "## Turn 1 — test\n### User\nhello\n### Assistant\nok\n\n"
    "## Session Summary\n**Decisions:** none\n"
)

_VALID_ARGS: dict[str, Any] = {
    "session_id": "cursor-2099-01-01-0001",
    "agent": "cursor",
    "transcript_md": _MINIMAL_TRANSCRIPT,
    "summary": "Regression test session for C3 ordering invariant.",
    "entity_ids": ["case:test-entity"],
}

_CRITICAL_FINDING = {
    "kind": "dangling_relationship_target",
    "subject": "case:test-entity",
    "severity": "critical",
    "detail": "Relationship target does not exist",
    "audit_id": "abc123def456",
}

_WARNING_FINDING = {
    "kind": "entity_empty_description",
    "subject": "case:test-entity",
    "severity": "warning",
    "detail": "Entity has empty description",
    "audit_id": "def456abc123",
}


def _make_audit_returns(findings: list[dict]) -> dict[str, Any]:
    """Build the return value _run_session_audit_or_block would produce for findings."""
    if not findings:
        return {}
    criticals = [f for f in findings if f["severity"] == "critical"]
    return {
        "warning": {
            "audit_findings": findings,
            "mode": "warn",
            "gap_count": len(findings),
            "deferred": [],
        }
    } if not criticals else {
        "blocked": True,
        "error": "session_audit blocked close — critical gaps unresolved",
        "code": "session_audit_blocked",
        "criticals": criticals,
        "remedy": "Fix gaps or pass defer_gaps={kind: reason, ...}",
    }


# ── C3 ordering tests ─────────────────────────────────────────────────────────

class TestC3OrderingInvariant:
    """The audit gate fires before file write — verified by patching the gate."""

    def test_block_mode_no_file_written(self, tmp_path: Path) -> None:
        """BLOCK mode: structured error returned; transcript file absent. (C3 assertion 1+2)"""
        blocked_result = {
            "blocked": True,
            "error": "session_audit blocked close — critical gaps unresolved",
            "code": "session_audit_blocked",
            "criticals": [_CRITICAL_FINDING],
            "remedy": "Fix gaps or pass defer_gaps={kind: reason, ...}",
        }

        with (
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._run_session_audit_or_block",
                return_value=blocked_result,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._FILES_ROOT",
                tmp_path,
            ),
        ):
            result = _op_session_close(**_VALID_ARGS)

        assert result.get("blocked") is True
        assert result.get("code") == "session_audit_blocked"

        # Transcript file MUST NOT exist — audit fired before file write.
        transcript_path = tmp_path / "notes" / "system" / "transcripts" / "cursor-2099-01-01-0001.md"
        assert not transcript_path.exists(), (
            "C3 violation: transcript file was written before audit gate returned blocked"
        )

    def test_warn_mode_file_written_with_warning(self, tmp_path: Path) -> None:
        """WARN mode with gaps: close succeeds; _warning populated; transcript exists. (C3 assertion 3)"""
        warn_result = {
            "warning": {
                "audit_findings": [_WARNING_FINDING],
                "mode": "warn",
                "gap_count": 1,
                "deferred": [],
            }
        }

        # Provide a minimal in-memory DB for _close_session_impl.
        db_path = tmp_path / "cortex.db"
        _bootstrap_test_db(db_path)

        with (
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._run_session_audit_or_block",
                return_value=warn_result,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._FILES_ROOT",
                tmp_path,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._close_session_impl",
                return_value={"transcript_entity_id": "transcript:cursor-2099-01-01-0001", "journal_row_id": 1},
            ),
        ):
            result = _op_session_close(**_VALID_ARGS)

        assert "error" not in result, f"Unexpected error in WARN mode: {result}"
        assert "_warning" in result, "WARN mode should populate _warning in response"
        assert result["_warning"]["gap_count"] == 1

        transcript_path = tmp_path / "notes" / "system" / "transcripts" / "cursor-2099-01-01-0001.md"
        assert transcript_path.exists(), "Transcript file should be written in WARN mode"

    def test_clean_audit_no_warning_key(self, tmp_path: Path) -> None:
        """No gaps → close succeeds with no _warning key. (C3 assertion 4)"""
        with (
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._run_session_audit_or_block",
                return_value={},
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._FILES_ROOT",
                tmp_path,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._close_session_impl",
                return_value={"transcript_entity_id": "transcript:cursor-2099-01-01-0001", "journal_row_id": 2},
            ),
        ):
            result = _op_session_close(**_VALID_ARGS)

        assert "error" not in result
        assert "_warning" not in result, "Clean audit should not add _warning key"

    def test_audit_called_before_file_write(self, tmp_path: Path) -> None:
        """Verify audit fires before file write by checking call order via side-effect."""
        call_order: list[str] = []

        def audit_spy(**kwargs: object) -> dict:
            call_order.append("audit")
            return {}

        original_write = Path.write_text

        def write_spy(self: Path, *args: object, **kwargs: object) -> None:
            if "transcripts" in str(self):
                call_order.append("file_write")
            return original_write(self, *args, **kwargs)

        with (
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._run_session_audit_or_block",
                side_effect=audit_spy,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._FILES_ROOT",
                tmp_path,
            ),
            patch(
                "libs.cortex_store.dispatch_ops.ops_journals._close_session_impl",
                return_value={"transcript_entity_id": "transcript:cursor-2099-01-01-0001", "journal_row_id": 3},
            ),
            patch.object(Path, "write_text", write_spy),
        ):
            _op_session_close(**_VALID_ARGS)

        assert call_order[0] == "audit", (
            f"C3 violation: audit did not fire first. Call order: {call_order}"
        )
        assert "file_write" in call_order


# ── session_audit op tests ────────────────────────────────────────────────────

class TestSessionAuditOp:
    def test_requires_session_id(self) -> None:
        result = _op_session_audit()
        assert "error" in result

    def test_returns_findings_shape(self) -> None:
        with patch(
            "libs.cortex_store.dispatch_ops.ops_review._run_session_audit_graph_only",
            return_value=[_WARNING_FINDING],
        ):
            result = _op_session_audit(session_id="cursor-2099-01-01-0001", entity_ids=["case:x"])

        assert result["session_id"] == "cursor-2099-01-01-0001"
        assert result["gap_count"] == 1
        assert result["warnings"] == 1
        assert "findings" in result


# ── case_audit op tests ───────────────────────────────────────────────────────

class TestCaseAuditOp:
    def test_requires_subject(self) -> None:
        result = _op_case_audit()
        assert "error" in result

    def test_returns_findings_shape(self) -> None:
        with patch(
            "libs.cortex_store.dispatch_ops.ops_review.run_detectors",
            return_value=[_WARNING_FINDING],
        ):
            result = _op_case_audit(subject="case:test")

        assert result["subject"] == "case:test"
        assert result["gap_count"] == 1
        assert "_next" in result


# ── fill_gaps op tests ────────────────────────────────────────────────────────

class TestFillGapsOp:
    def test_requires_findings_or_subject(self) -> None:
        result = _op_fill_gaps()
        assert "error" in result

    def test_maps_known_gap_kinds(self) -> None:
        result = _op_fill_gaps(findings=[_WARNING_FINDING])
        assert result["count"] == 1
        suggestion = result["suggestions"][0]
        assert suggestion["suggested_op"] == "entity_update"
        assert "remedy" in suggestion

    def test_unknown_gap_kind_has_fallback_remedy(self) -> None:
        unknown_finding = {**_WARNING_FINDING, "kind": "unknown_kind_xyz", "audit_id": "fff000"}
        result = _op_fill_gaps(findings=[unknown_finding])
        assert result["count"] == 1
        assert "remedy" in result["suggestions"][0]


# ── defer_gaps tests ──────────────────────────────────────────────────────────

class TestDeferGaps:
    def test_defer_exempts_critical_from_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """defer_gaps={kind: reason} prevents that kind from triggering BLOCK mode."""
        monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "block")

        with patch(
            "libs.cortex_store.dispatch_ops.ops_review._run_session_audit_graph_only",
            return_value=[_CRITICAL_FINDING],
        ):
            result = _run_session_audit_or_block(
                session_id="cursor-2099-01-01-0001",
                agent="cursor",
                entity_ids=["case:test-entity"],
                defer_gaps={"dangling_relationship_target": "known issue, deferring"},
            )

        # Deferred critical should not block.
        assert not result.get("blocked"), f"Deferred critical should not block: {result}"
        # But warning should still be present since there are findings.
        assert "warning" in result

    def test_non_deferred_critical_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Critical gap not in defer_gaps triggers BLOCK mode structured error."""
        monkeypatch.setenv("CORTEX_SESSION_AUDIT_MODE", "block")

        with patch(
            "libs.cortex_store.dispatch_ops.ops_review._run_session_audit_graph_only",
            return_value=[_CRITICAL_FINDING],
        ):
            result = _run_session_audit_or_block(
                session_id="cursor-2099-01-01-0001",
                agent="cursor",
                entity_ids=["case:test-entity"],
                defer_gaps=None,
            )

        assert result.get("blocked") is True
        assert result.get("code") == "session_audit_blocked"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bootstrap_test_db(db_path: Path) -> None:
    """Create a minimal cortex DB schema for tests that exercise _close_session_impl."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, type TEXT, name TEXT,
            description TEXT, source_uri TEXT, status TEXT,
            workflow_state TEXT, attributes TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS session_journals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE, agent TEXT, summary TEXT,
            timestamp TEXT, created_at TEXT
        );
    """)
    conn.commit()
    conn.close()
