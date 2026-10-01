"""7065#1417 — process_live obligation prose and envelope proof field honesty."""

from __future__ import annotations

from implement_admission.propagation_row import PropagationRow, compose_proof

from services.git_integration_worker.relay.propagation_probe import (
    AGE_FIELDS,
    IDENTIFIER_FIELDS,
    proof_observed,
)


def _sample_before() -> dict:
    return {
        "pid": 100,
        "process_start_time": "2026-01-01T00:00:00Z",
        "code_version": "abc",
    }


def _sample_after() -> dict:
    return {
        "pid": 200,
        "process_start_time": "2026-01-02T00:00:00Z",
        "code_version": "abc",
    }


def test_identity_movement_is_required_for_sha_attributed_proof() -> None:
    """Matching code alone cannot establish the stronger live claim."""
    sha = "abc1230000000000000000000000000000000000"
    row = PropagationRow(
        service="git_integration_worker",
        code_ref=sha,
        proof_class="process_live",
    )
    unchanged = _sample_before() | {"code_version": sha}
    moved = _sample_after() | {"code_version": sha}
    assert proof_observed(row, unchanged, before=_sample_before()) is False
    assert proof_observed(row, moved, before=_sample_before()) is True


# --- AC1 fail-first (must fail against current code before fixes) ---


def test_process_live_obligation_prose_names_no_age_fields() -> None:
    """Obligation must not name AGE_FIELDS — attestation never uses them."""
    proof = compose_proof("mcp", "process_live")
    for field in AGE_FIELDS:
        assert field not in proof, (
            f"age field {field!r} must not appear in obligation prose"
        )




# --- (A) derivation / drift ---


def test_process_live_obligation_names_all_identifier_fields() -> None:
    proof = compose_proof("stargate", "process_live")
    for field in IDENTIFIER_FIELDS:
        assert field in proof, (
            f"identifier field {field!r} must appear in obligation prose"
        )


def test_process_live_obligation_identity_clause_derived_from_identifier_fields() -> (
    None
):
    """Prose identity list must match IDENTIFIER_FIELDS join — not hand-copied."""
    proof = compose_proof("gateway", "process_live")
    expected_clause = "/".join(IDENTIFIER_FIELDS)
    assert expected_clause in proof


# --- (B) state table — one test per row ---












    # No execution dict exists — proof keys are absent by construction.
