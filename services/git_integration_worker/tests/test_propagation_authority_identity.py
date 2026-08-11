"""Falsifier tests for authority-primary propagation identity attestation (Option C).

These tests encode AC4 and AC9–AC11: attestation must not widen what counts as
proof — indeterminate-rate dashboards are not quality gates; these falsifiers are.
"""

from __future__ import annotations

from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.propagation_probe import (
    attest_authority_identity,
    attest_identity_delta,
    proof_observed,
    resolve_identity_attestation,
)

_SHA = "abc1230000000000000000000000000000000000"


def _process_live_row(*, service: str = "stargate", code_ref: str = _SHA) -> PropagationRow:
    return PropagationRow(
        service=service,
        code_ref=code_ref,
        safe_window="standalone_ok",
        proof="test probe",
        proof_class="process_live",
    )


def test_age_alone_never_closes_identity_arm() -> None:
    """AC4-1 / AC5: age counters alone do not attest identity movement."""
    before = {"code_version": _SHA, "process_age_s": 10, "uptime_s": 10}
    after = {"code_version": _SHA, "process_age_s": 1, "uptime_s": 1}
    assert attest_identity_delta(before, after) == "indeterminate"
    row = _process_live_row()
    assert proof_observed(row, after, before=before) is False


def test_authority_without_readiness_proven_never_closes() -> None:
    """AC4-2 / AC6: authority identity without readiness join does not close."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": False,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    assert attest_authority_identity(authority) == "fall_through"
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )


def test_mcp_pid_one_alone_never_closes() -> None:
    """AC4-3 / AC4: mcp pid==1 alone is stripped and does not attest."""
    before = {"pid": 1, "code_version": _SHA}
    after = {"pid": 1, "code_version": _SHA}
    assert attest_identity_delta(before, after, service="mcp") == "indeterminate"
    row = _process_live_row(service="mcp")
    assert proof_observed(row, after, before=before) is False


def test_code_version_without_identity_delta_does_not_close() -> None:
    """AC4-4: matching code_version without identifier delta does not close."""
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    row = _process_live_row()
    assert proof_observed(row, after, before=before) is False


def test_authority_readiness_old_ne_new_closes_without_health_pid() -> None:
    """AC4-5 / AC1: authority+readiness with old≠new closes when health lacks pid."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    assert attest_authority_identity(authority) == "changed"
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is True
    )


def test_ac9_authority_unchanged_terminal_blocks_self_report_changed() -> None:
    """AC9: same authority old/new is unchanged TERMINAL — self-report cannot overturn."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 100,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA, "pid": 100}
    after = {"code_version": _SHA, "pid": 999}
    assert attest_authority_identity(authority) == "unchanged"
    assert (
        resolve_identity_attestation(
            before,
            after,
            service="stargate",
            authority_identity=authority,
        )
        == "unchanged"
    )
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )


def test_ac10_authority_partial_old_or_new_falls_through_never_changed() -> None:
    """AC10: None old or new is not an authority delta; never authority changed."""
    authority = {
        "service": "stargate",
        "old": None,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    assert attest_authority_identity(authority) == "fall_through"
    assert (
        resolve_identity_attestation(
            before,
            after,
            service="stargate",
            authority_identity=authority,
        )
        == "indeterminate"
    )
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )


def test_ac11_cross_source_identity_never_authority_changed() -> None:
    """AC11: mismatched identity sources fall through; never authority changed."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": "2026-08-11T10:00:00.000000000Z",
        "identity_source": "manage_host_pid",
        "old_identity_source": "manage_host_pid",
        "new_identity_source": "manage_container_started_at",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    assert attest_authority_identity(authority) == "fall_through"
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )
