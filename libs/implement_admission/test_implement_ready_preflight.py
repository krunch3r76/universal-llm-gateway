"""Parity and gate-status tests for preflight_implement_ready."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import evaluate_implement_ready
from implement_admission.implement_ready_preflight import (
    GateStatus,
    preflight_implement_ready,
)
from implement_admission.recon_waiver import (
    build_structured_waiver,
    resolve_effective_recon_waived,
    waiver_matches_current_spec,
)

NOW = datetime.now(UTC).isoformat()
SPEC_TEXT_VALID = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the validator.

## 7. Acceptance criteria

1. Validator passes dense specs.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""

BASE = dict(
    todo_id="todo:test-slug",
    density_triage="judgment_required",
    source_uri="tasks/specs/test-slug.md",
    implement_ready_assertion_id=42,
    assertion={
        "id": 42,
        "entity_id": "todo:test-slug",
        "superseded_by": None,
        "valid_until": None,
        "evidence_uris": [],
    },
    now_iso=NOW,
    dense_spec_uri="tasks/specs/test-slug.md",
    dense_spec_text=SPEC_TEXT_VALID,
    files_expected=["libs/implement_admission/implement_ready.py"],
    acceptance_criteria=["Gate 1 passes"],
    entity_name="Test todo",
    consult_thread="agent-bus:8801",
    verdict="proceed",
    consultant_family="anthropic",
    consultant_substrate="web-anthropic",
)


def make(**overrides: object) -> dict:
    return {**BASE, **overrides}


def parity(*, verdict, preflight_report) -> None:
    if not verdict.admitted:
        assert preflight_report.first_failure is not None
        assert preflight_report.first_failure["code"] == verdict.code
    else:
        assert preflight_report.admitted is True


def _ready_evidence() -> list[str]:
    return ["tasks/specs/test-slug.md", dense_spec_hash_uri(SPEC_TEXT_VALID)]


def _structured_waiver(*, spec_sha256: str) -> str:
    return build_structured_waiver(
        reason_code="operator_directive",
        reason="test waiver",
        waived_by="test-agent",
        spec_sha256=spec_sha256,
    ).to_attr_json()


_CURRENT_SPEC_HASH = dense_spec_hash_uri(SPEC_TEXT_VALID)
_STALE_SPEC_HASH = "spec_sha256:" + ("a" * 64)


@pytest.mark.offline
def test_waiver_matches_current_spec_honors_matching_hash() -> None:
    waiver = build_structured_waiver(
        reason_code="operator_directive",
        reason="test",
        waived_by="test",
        spec_sha256=_CURRENT_SPEC_HASH,
    )
    assert waiver_matches_current_spec(waiver, _CURRENT_SPEC_HASH) is True
    assert waiver_matches_current_spec(waiver, _CURRENT_SPEC_HASH.removeprefix("spec_sha256:")) is True


@pytest.mark.offline
def test_waiver_matches_current_spec_rejects_stale_hash() -> None:
    waiver = build_structured_waiver(
        reason_code="operator_directive",
        reason="test",
        waived_by="test",
        spec_sha256=_STALE_SPEC_HASH,
    )
    assert waiver_matches_current_spec(waiver, _CURRENT_SPEC_HASH) is False


@pytest.mark.offline
def test_waiver_matches_current_spec_fail_closed_when_spec_unreadable() -> None:
    waiver = build_structured_waiver(
        reason_code="operator_directive",
        reason="test",
        waived_by="test",
        spec_sha256=_CURRENT_SPEC_HASH,
    )
    assert waiver_matches_current_spec(waiver, None) is False


@pytest.mark.offline
def test_resolve_effective_recon_waived_stale_discarded() -> None:
    raw = _structured_waiver(spec_sha256=_STALE_SPEC_HASH)
    effective, waiver, stale = resolve_effective_recon_waived(raw, _CURRENT_SPEC_HASH)
    assert effective is False
    assert waiver is not None
    assert stale is True


@pytest.mark.offline
def test_resolve_effective_recon_waived_fail_closed_when_spec_unreadable() -> None:
    raw = _structured_waiver(spec_sha256=_CURRENT_SPEC_HASH)
    effective, waiver, stale = resolve_effective_recon_waived(raw, None)
    assert effective is False
    assert waiver is not None
    assert stale is True


@pytest.mark.offline
def test_resolve_effective_recon_waived_honors_current_hash() -> None:
    raw = _structured_waiver(spec_sha256=_CURRENT_SPEC_HASH)
    effective, waiver, stale = resolve_effective_recon_waived(raw, _CURRENT_SPEC_HASH)
    assert effective is True
    assert waiver is not None
    assert stale is False


@pytest.mark.offline
def test_gate13_stale_recon_waiver_requires_skeptic_ratification() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    raw = _structured_waiver(spec_sha256=_STALE_SPEC_HASH)
    recon_waived, _, _ = resolve_effective_recon_waived(raw, _CURRENT_SPEC_HASH)
    assert recon_waived is False
    args = make(
        assertion=assertion,
        skeptic_ratified=False,
        recon_waived=recon_waived,
        check_requested=True,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[13].status == GateStatus.FAILED
    assert report.gates[13].code == "skeptic_pass_missing"
    assert verdict.admitted is False
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate0_mechanical_bypass() -> None:
    args = make(density_triage="mechanical")
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert verdict.admitted is True
    assert report.admitted is True
    assert len(report.gates) == 15
    assert report.gates[0].status == GateStatus.PASSED
    assert all(g.status == GateStatus.NOT_APPLICABLE for g in report.gates[1:])
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate1_unknown_triage() -> None:
    args = make(density_triage=None)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[1].status == GateStatus.FAILED
    assert report.gates[1].code == "implement_triage_unknown"
    assert report.gates[1].reason is not None
    assert "mechanical (bypass implement-ready gates)" in report.gates[1].reason
    assert all(report.gates[i].status == GateStatus.BLOCKED for i in range(2, 14))
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate1_recon_pending() -> None:
    args = make(density_triage="recon_pending")
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[1].status == GateStatus.FAILED
    assert report.gates[1].code == "implement_blocked_recon_pending"
    assert all(report.gates[i].status == GateStatus.BLOCKED for i in range(2, 14))
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate2_missing_pin() -> None:
    args = make(implement_ready_assertion_id=None, assertion=None)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[2].status == GateStatus.FAILED
    assert report.gates[2].code == "implement_not_ready_judgment_required"
    assert report.gates[3].status == GateStatus.BLOCKED
    assert report.gates[4].status == GateStatus.BLOCKED
    assert report.gates[5].status == GateStatus.BLOCKED
    assert report.gates[6].status in (GateStatus.PASSED, GateStatus.FAILED)
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate3_assertion_missing() -> None:
    args = make(assertion=None)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[3].status == GateStatus.FAILED
    assert report.gates[3].code == "implement_ready_assertion_missing"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate4_entity_mismatch() -> None:
    assertion = {**BASE["assertion"], "entity_id": "todo:other-slug"}
    args = make(assertion=assertion)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[4].status == GateStatus.FAILED
    assert report.gates[4].code == "implement_ready_assertion_entity_mismatch"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate5_assertion_inactive() -> None:
    assertion = {**BASE["assertion"], "superseded_by": 99}
    args = make(assertion=assertion)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[5].status == GateStatus.FAILED
    assert report.gates[5].code == "implement_ready_assertion_inactive"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate6_no_dense_spec() -> None:
    args = make(source_uri="")
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[6].status == GateStatus.FAILED
    assert report.gates[6].code == "implement_not_ready_no_dense_spec"
    for i in (7, 8, 9, 10):
        assert report.gates[i].status == GateStatus.BLOCKED
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate7_spec_uncited() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": []}
    args = make(assertion=assertion)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[7].status == GateStatus.FAILED
    assert report.gates[7].code == "implement_ready_assertion_spec_uncited"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate8_spec_unreadable() -> None:
    args = make(dense_spec_text=None)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[8].status == GateStatus.FAILED
    assert report.gates[8].code == "implement_spec_unreadable"
    assert report.gates[9].status == GateStatus.BLOCKED
    assert report.gates[10].status == GateStatus.BLOCKED
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate9_schema_failure() -> None:
    args = make(dense_spec_text="# not a valid dense spec")
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[9].status == GateStatus.FAILED
    assert report.gates[9].code == "implement_spec_not_dense"
    assert report.gates[10].status == GateStatus.BLOCKED
    assert 9 in report.gates[10].blocked_by
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate10_hash_drift() -> None:
    assertion = {
        **BASE["assertion"],
        "evidence_uris": ["tasks/specs/test-slug.md"],
    }
    args = make(assertion=assertion, dense_spec_text=SPEC_TEXT_VALID)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[10].status == GateStatus.FAILED
    assert report.gates[10].code == "implement_spec_drifted_since_ready"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate11_files_expected_empty() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(assertion=assertion, files_expected=[])
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[11].status == GateStatus.FAILED
    assert report.gates[11].code == "implement_attrs_unpopulated"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate12_acs_empty() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(assertion=assertion, acceptance_criteria=[])
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[12].status == GateStatus.FAILED
    assert report.gates[12].code == "implement_attrs_unpopulated"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_happy_path_all_pass() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(assertion=assertion, skeptic_ratified=True, check_requested=True)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert verdict.admitted is True
    assert report.admitted is True
    assert len(report.gates) == 15
    assert report.gates[0].status == GateStatus.NOT_APPLICABLE
    assert all(g.status == GateStatus.PASSED for g in report.gates[1:])
    assert report.first_failure is None
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate13_skeptic_pass_missing() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=False,
        recon_waived=False,
        check_requested=True,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[13].status == GateStatus.FAILED
    assert report.gates[13].code == "skeptic_pass_missing"
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate13_default_skips_when_check_not_requested() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(assertion=assertion, skeptic_ratified=False, recon_waived=False)
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert verdict.admitted is True
    assert report.admitted is True
    assert report.gates[13].status == GateStatus.NOT_APPLICABLE
    parity(verdict=verdict, preflight_report=report)


_GATE_13_DEFERRED_SUBCHECKS = [
    "skeptic_evidence_grounded",
    "skeptic_evidence_unresolved",
    "skeptic_evidence_mode",
]


@pytest.mark.offline
def test_gate13_pass_deferred_subchecks_annotation() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(assertion=assertion, skeptic_ratified=True, check_requested=True)
    report = preflight_implement_ready(**args)
    gate13 = report.gates[13]
    assert gate13.status == GateStatus.PASSED
    assert list(gate13.deferred_subchecks) == _GATE_13_DEFERRED_SUBCHECKS
    assert report.to_dict()["gates"][13]["deferred_subchecks"] == _GATE_13_DEFERRED_SUBCHECKS


@pytest.mark.offline
def test_gate13_recon_waived_bypasses_skeptic() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=False,
        recon_waived=True,
        check_requested=True,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert report.gates[13].status == GateStatus.PASSED
    assert verdict.admitted is True
    parity(verdict=verdict, preflight_report=report)


@pytest.mark.offline
def test_gate14_deferred_grounding_admits_with_explicit_warning() -> None:
    # When grounding inputs are absent, preflight still admits on the
    # declared-state gates, but MUST carry an explicit warning naming the
    # FILE_EVIDENCE_PATHS requirement and the skeptic_evidence_missing code
    # the dispatch would emit (friction 22906 — no silent divergence).
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=True,
        recon_waived=False,
        check_requested=True,
        skeptic_evidence_grounded=False,
        skeptic_evidence_unresolved=None,
        skeptic_evidence_mode=None,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(
        **{k: v for k, v in args.items() if k != "skeptic_evidence_grounded"
           and k != "skeptic_evidence_unresolved" and k != "skeptic_evidence_mode"}
    )
    assert verdict.admitted is False
    assert verdict.code == "skeptic_evidence_missing"
    assert report.admitted is True
    assert len(report.gates) == 15
    assert report.warnings, "deferred grounding must surface a warning"
    joined = " ".join(report.warnings)
    assert "FILE_EVIDENCE_PATHS" in joined
    assert "skeptic_evidence_missing" in joined
    assert report.to_dict()["warnings"] == report.warnings


@pytest.mark.offline
def test_gate13_evidence_supplied_matches_dispatch_reject() -> None:
    # Preflight/dispatch parity: with grounding inputs supplied, gate 13
    # fails with the same code the implement dispatch would emit.
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=True,
        recon_waived=False,
        check_requested=True,
        skeptic_evidence_grounded=False,
        skeptic_evidence_unresolved=None,
        skeptic_evidence_mode=None,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert verdict.admitted is False
    assert report.admitted is False
    assert report.gates[13].status == GateStatus.FAILED
    assert report.gates[13].code == verdict.code == "skeptic_evidence_missing"
    assert not report.warnings


@pytest.mark.offline
def test_gate13_evidence_grounded_passes_without_warning() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=True,
        recon_waived=False,
        check_requested=True,
        skeptic_evidence_grounded=True,
    )
    verdict = evaluate_implement_ready(**args)
    report = preflight_implement_ready(**args)
    assert verdict.admitted is True
    assert report.admitted is True
    assert report.gates[13].status == GateStatus.PASSED
    assert not report.gates[13].deferred_subchecks
    assert not report.warnings


@pytest.mark.offline
def test_gate13_skeptic_pass_missing_reason_carries_subcondition() -> None:
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=False,
        recon_waived=False,
        check_requested=True,
        skeptic_unratified_reason="evidence_uris lacks the spec_sha256:<hex> URI",
    )
    report = preflight_implement_ready(**args)
    gate13 = report.gates[13]
    assert gate13.status == GateStatus.FAILED
    assert gate13.code == "skeptic_pass_missing"
    assert gate13.reason is not None
    assert "Unmet subcondition" in gate13.reason
    assert "spec_sha256" in gate13.reason
