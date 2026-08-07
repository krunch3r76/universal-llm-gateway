"""Regression: unknown assertion_update dispatch keys must not fail silently."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from cortex_store.dispatch_ops.ops_assertions_update import (
    _ASSERTION_UPDATE_ACCEPTED_KEYS,
    _op_assertion_update,
)
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.dispatch_ops.workflow_hints import _WORKFLOW_HINTS

pytestmark = pytest.mark.offline

_STALE_IMMUTABLE_PHRASE = "reasoning_summary is immutable"
_STALE_NOT_ACCEPTED_PHRASE = "assertion_update does not accept it"


def test_assertion_update_mixed_patchable_and_dropped_keys_surfaces_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: patchable + non-whitelist keys → 200 row + validation_warnings."""
    seen: dict[str, object] = {}

    def _fake_impl(assertion_id: int, body: dict[str, object]) -> dict[str, object]:
        seen.update(body)
        return {"id": assertion_id, "review_notes": body.get("review_notes")}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_update._update_assertion_impl",
        _fake_impl,
    )
    result = _op_assertion_update(
        assertion_id=42,
        review_notes="retirement note",
        evidence="free-text evidence",
        raw_predicate_form="legacy",
    )

    assert "error" not in result
    assert seen.get("review_notes") == "retirement note"
    warnings = result.get("validation_warnings") or []
    dropped_fields = {w["field"] for w in warnings}
    assert dropped_fields == {"evidence", "raw_predicate_form"}
    for warning in warnings:
        assert warning["category"] == "dispatch"
        assert "Accepted keys:" in warning["message"]
        for accepted in _ASSERTION_UPDATE_ACCEPTED_KEYS:
            assert accepted in warning["message"]


def test_assertion_update_only_dropped_keys_names_them_in_no_fields_error() -> None:
    """AC2: only non-whitelist keys → No fields to update + dropped key names."""
    result = _op_assertion_update(
        assertion_id=7,
        evidence="only dropped",
        chunk_id="also dropped",
    )

    assert result.get("error") == "No fields to update"
    warnings = result.get("validation_warnings") or []
    assert {w["field"] for w in warnings} == {"chunk_id", "evidence"}
    assert all(w["category"] == "dispatch" for w in warnings)


def test_stale_reasoning_summary_immutability_strings_removed_from_hints() -> None:
    """AC3: ops_assertions_write + workflow_hints no longer claim immutability."""
    assert_source = inspect.getsource(_op_assert)
    assert _STALE_IMMUTABLE_PHRASE not in assert_source
    assert _STALE_NOT_ACCEPTED_PHRASE not in assert_source

    session_close_hint = _WORKFLOW_HINTS["session_close"]
    assert _STALE_IMMUTABLE_PHRASE not in session_close_hint
    assert "assertion_update accepts reasoning_summary" in session_close_hint

    write_module = Path(__file__).resolve().parents[1] / "dispatch_ops" / "ops_assertions_write.py"
    write_text = write_module.read_text()
    assert _STALE_IMMUTABLE_PHRASE not in write_text
    assert _STALE_NOT_ACCEPTED_PHRASE not in write_text
