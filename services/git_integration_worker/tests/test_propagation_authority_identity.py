"""Falsifier tests for authority-primary propagation identity attestation (Option C).

These tests encode AC4 and AC9–AC14: attestation must not widen what counts as
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


def test_ac12_readiness_proven_requires_exact_true() -> None:
    """AC12 / L2: only ``readiness_proven is True`` proceeds; other values fall through."""
    base = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
    }
    assert attest_authority_identity({**base, "readiness_proven": True}) == "changed"
    assert attest_authority_identity({**base, "readiness_proven": False}) == "fall_through"
    assert attest_authority_identity({**base, "readiness_proven": "yes"}) == "fall_through"
    assert attest_authority_identity({**base, "readiness_proven": 1}) == "fall_through"
    assert attest_authority_identity({**base, "readiness_proven": ["x"]}) == "fall_through"
    assert attest_authority_identity({**base}) == "fall_through"


def test_ac13_normalize_first_blank_never_authority_changed() -> None:
    """AC13 / L3: blank or whitespace old/new normalize to None and never ``changed``."""
    base = {
        "service": "stargate",
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    for old in ("", "   ", None):
        authority = {**base, "old": old}
        assert attest_authority_identity(authority) == "fall_through", f"old={old!r}"


def test_ac13_normalize_first_both_blank_fall_through_not_unchanged() -> None:
    """AC13 / L3: both sides blank normalize to None ⇒ fall_through, not unchanged."""
    authority = {
        "service": "stargate",
        "old": "",
        "new": "   ",
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    assert attest_authority_identity(authority) == "fall_through"


def test_ac13_normalize_first_equal_non_blank_unchanged() -> None:
    """AC13 / L3: both normalize to same non-None value ⇒ unchanged (AC9 path)."""
    authority = {
        "service": "stargate",
        "old": "100",
        "new": 100,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    assert attest_authority_identity(authority) == "unchanged"


def test_ac13_normalize_first_differing_non_blank_changed() -> None:
    """AC13 / L3: both normalize non-None and differ ⇒ changed."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    assert attest_authority_identity(authority) == "changed"


def test_ac14_authority_service_must_match_row() -> None:
    """AC14 / L5: authority service must equal row service or attestation falls through."""
    authority = {
        "service": "mcp",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    row = _process_live_row(service="stargate")
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )
    assert (
        resolve_identity_attestation(
            before,
            after,
            service="stargate",
            authority_identity=authority,
        )
        == "indeterminate"
    )


def test_ac14_authority_without_service_falls_through() -> None:
    """AC14 / L5: authority record without service cannot bind a row."""
    authority = {
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    row = _process_live_row()
    assert attest_authority_identity(authority) == "changed"
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is False
    )


def test_ac14_intent_id_mismatch_falls_through_when_both_present() -> None:
    """AC14 / L5: mismatched intent_id falls through when both sides carry one."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
        "intent_id": "intent-a",
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    row = _process_live_row()
    assert (
        proof_observed(
            row,
            after,
            before=before,
            authority_identity=authority,
            intent_id="intent-b",
        )
        is False
    )


def test_ac14_intent_id_not_blocked_when_either_side_absent() -> None:
    """AC14 / L5: intent_id mismatch is not checked when either side lacks intent_id."""
    authority = {
        "service": "stargate",
        "old": 100,
        "new": 200,
        "identity_source": "manage_host_pid",
        "readiness_proven": True,
        "intent_id": "intent-a",
    }
    before = {"code_version": _SHA}
    after = {"code_version": _SHA}
    row = _process_live_row()
    assert (
        proof_observed(row, after, before=before, authority_identity=authority)
        is True
    )
    authority_no_intent = {k: v for k, v in authority.items() if k != "intent_id"}
    assert (
        proof_observed(
            row,
            after,
            before=before,
            authority_identity=authority_no_intent,
            intent_id="intent-b",
        )
        is True
    )
