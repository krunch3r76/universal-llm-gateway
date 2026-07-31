"""Unit tests for Phase 2.0 user-callable review ops (ops_review.py).

Covers session_audit, case_audit, fill_gaps ops.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from libs.cortex_store.dispatch_ops.ops_review import (
    _op_case_audit,
    _op_fill_gaps,
    _op_session_audit,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_WARNING_FINDING: dict[str, Any] = {
    "kind": "entity_empty_description",
    "subject": "case:test-entity",
    "severity": "warning",
    "detail": "Entity has empty description",
    "audit_id": "def456abc123",
}


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

    def test_subject_path_uses_graph_only_by_default(self) -> None:
        """fill_gaps(subject=...) should call case_audit with include_filesystem=False."""
        with patch(
            "libs.cortex_store.dispatch_ops.ops_review._op_case_audit",
            return_value={"findings": [_WARNING_FINDING], "gap_count": 1},
        ) as mock_audit:
            _op_fill_gaps(subject="case:test")

        mock_audit.assert_called_once_with(subject="case:test", include_filesystem=False)
