"""7065#1417 — process_live obligation prose and envelope proof field honesty."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from implement_admission.propagation_row import PropagationRow, compose_proof
from services.git_integration_worker.cursor_auto.handler_propagation import (
    _disposition_for,
    execution_for_manage_deferred,
    execution_terminal_proof_fields,
)
from services.git_integration_worker.cursor_auto.propagation_probe import (
    AGE_FIELDS,
    IDENTIFIER_FIELDS,
)


def _sample_before() -> dict:
    return {"pid": 100, "process_start_time": "2026-01-01T00:00:00Z", "code_version": "abc"}

def _sample_after() -> dict:
    return {"pid": 200, "process_start_time": "2026-01-02T00:00:00Z", "code_version": "abc"}


# --- AC1 fail-first (must fail against current code before fixes) ---


def test_process_live_obligation_prose_names_no_age_fields() -> None:
    """Obligation must not name AGE_FIELDS — attestation never uses them."""
    proof = compose_proof("mcp", "process_live")
    for field in AGE_FIELDS:
        assert field not in proof, f"age field {field!r} must not appear in obligation prose"


def test_submitted_execution_has_no_after_asserting_proof_key() -> None:
    """Submitted rows must not expose submit-time capture as ``proof``."""
    fields = execution_terminal_proof_fields(
        status="submitted",
        before=_sample_before(),
        after=_sample_after(),
    )
    assert "proof" not in fields


# --- (A) derivation / drift ---


def test_process_live_obligation_names_all_identifier_fields() -> None:
    proof = compose_proof("stargate", "process_live")
    for field in IDENTIFIER_FIELDS:
        assert field in proof, f"identifier field {field!r} must appear in obligation prose"


def test_process_live_obligation_identity_clause_derived_from_identifier_fields() -> None:
    """Prose identity list must match IDENTIFIER_FIELDS join — not hand-copied."""
    proof = compose_proof("gateway", "process_live")
    expected_clause = "/".join(IDENTIFIER_FIELDS)
    assert expected_clause in proof


# --- (B) state table — one test per row ---


def test_envelope_executed_row_keeps_proof() -> None:
    fields = execution_terminal_proof_fields(
        status="executed",
        before=_sample_before(),
        after=_sample_after(),
    )
    assert "proof" in fields
    assert fields["proof"] == _sample_after()
    assert fields["proof_before"] == _sample_before()


def test_envelope_propagated_disposition_from_executed_row_keeps_proof() -> None:
    execution = {
        "service": "mcp",
        "status": "executed",
        **execution_terminal_proof_fields(
            status="executed",
            before=_sample_before(),
            after=_sample_after(),
        ),
    }
    assert _disposition_for([execution]) == "propagated"
    assert "proof" in execution


def test_envelope_submitted_has_time_honest_capture_not_proof() -> None:
    fields = execution_terminal_proof_fields(
        status="submitted",
        before=_sample_before(),
        after=_sample_after(),
    )
    assert "proof" not in fields
    assert "proof_at_submit" in fields
    assert fields["proof_at_submit"] == _sample_after()
    assert "proof_at_submit_captured_at" in fields
    datetime.fromisoformat(fields["proof_at_submit_captured_at"])


def test_envelope_queued_execution_has_no_proof_or_at_submit() -> None:
    row = PropagationRow(service="mcp", code_ref="deadbeef", proof_class="client_visible")
    with patch(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
    ):
        result = execution_for_manage_deferred(
            row,
            row_id="mcp:deadbeef:sync_restart",
            manage_result={
                "status": "deferred",
                "restart_intent_id": "intent-1",
                "reason": "draining",
            },
        )
    assert result["status"] == "queued"
    assert "proof" not in result
    assert "proof_at_submit" not in result


def test_envelope_blocked_execution_has_no_proof_or_at_submit() -> None:
    execution = {"service": "mcp", "status": "blocked", "reason": "busy"}
    assert _disposition_for([execution]) == "blocked"
    assert "proof" not in execution
    assert "proof_at_submit" not in execution


def test_envelope_no_executions_field_absent_not_empty_proof() -> None:
    assert _disposition_for([]) == "failed"
    # No execution dict exists — proof keys are absent by construction.

