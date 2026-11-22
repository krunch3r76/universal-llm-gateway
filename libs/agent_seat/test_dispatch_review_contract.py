"""Tests for review dispatch appendix, coverage manifest, and positive gates."""

from __future__ import annotations

import hashlib

import pytest

from agent_seat.dispatch_review_contract import (
    FREEFORM_LADDER_REVIEW_ROUTE,
    REVIEW_COVERAGE_GATE_EVENT,
    REVIEW_COVERAGE_GATE_EVENT_ACTION,
    UNSUPPORTED_ROUTE_PREFIX,
    CoverageManifest,
    CoverageTargetRow,
    blocking_survives_incomplete_coverage,
    compact_envelope_coverage_ref,
    evaluate_envelope_receipt_authority,
    evaluate_positive_whole_file_gate,
    event_vocabulary_row,
    freeform_ladder_review_coverage_appendix,
    harvest_promoted_verdict_with_coverage,
    legacy_verdict_without_coverage,
    parse_coverage_ref_line,
    resolve_review_route,
    review_dispatch_system_appendix,
    validate_extent_authorized,
)

pytestmark = pytest.mark.offline

_SHA = "a" * 64
_WORKER = "cursor-sdk:24871"
_FILE = "libs/foo.py"
_RECEIPT = b"whole file receipt bytes for gate test\n"


def _row(**overrides: object) -> CoverageTargetRow:
    base = dict(
        target=_FILE,
        pin=_SHA,
        required_extent="whole",
        actual_reader=_WORKER,
        actual_extent="whole",
        receipt_uri="cortex://notes/tmp/receipts/foo.md",
        receipt_digest=hashlib.sha256(_RECEIPT).hexdigest(),
        file_length=120,
    )
    base.update(overrides)
    return CoverageTargetRow(**base)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> CoverageManifest:
    base = dict(
        review_id="disp-1",
        policy_route="code-review",
        reviewed_pin=_SHA,
        verdict_line=f"VERDICT: APPROVE range={_SHA}..{_SHA} head={_SHA}",
        scope=[_FILE],
        targets=[_row()],
    )
    base.update(overrides)
    return CoverageManifest(**base)  # type: ignore[arg-type]


def test_review_appendix_injected_for_code_review() -> None:
    appendix = review_dispatch_system_appendix("code-review")
    assert appendix is not None
    assert "REVIEW VERDICT CONTRACT" in appendix
    assert "REVIEW COVERAGE CONTRACT" in appendix
    assert "CODE REVIEW INSPECTION" in appendix


@pytest.mark.parametrize("contract", ["code-review", "check-review", "delivery-review"])
def test_review_appendix_coverage_for_review_contracts(contract: str) -> None:
    appendix = review_dispatch_system_appendix(contract)
    assert appendix is not None
    assert "REVIEW COVERAGE CONTRACT" in appendix
    resolution = resolve_review_route(contract)
    assert resolution is not None
    assert resolution.supported is True


def test_freeform_ladder_review_requests_coverage() -> None:
    packet = "policy_route: freeform-ladder\nLeg 4 — Pre-land review\n"
    appendix = review_dispatch_system_appendix("freeform", packet_text=packet)
    assert appendix is not None
    assert "REVIEW COVERAGE CONTRACT" in appendix
    resolution = resolve_review_route("freeform", packet_text=packet)
    assert resolution is not None
    assert resolution.policy_route == FREEFORM_LADDER_REVIEW_ROUTE
    assert freeform_ladder_review_coverage_appendix().startswith(
        "REVIEW VERDICT CONTRACT"
    )


def test_review_appendix_none_for_plain_freeform() -> None:
    assert review_dispatch_system_appendix("freeform") is None
    assert review_dispatch_system_appendix(None) is None


def test_review_appendix_unsupported_freeform_review_without_ladder() -> None:
    packet = "contract: freeform\nPlease run a code review.\n"
    text = review_dispatch_system_appendix("freeform", packet_text=packet)
    assert text is not None
    assert text.startswith(UNSUPPORTED_ROUTE_PREFIX)


def test_harvest_keeps_single_verdict_with_coverage_rows() -> None:
    body = (
        "status: complete\n"
        f"VERDICT: APPROVE range={_SHA}..{_SHA} head={_SHA}\n"
        "coverage_ref: cortex://notes/tmp/cov.json#sha256=abc123\n"
        "| target | extent |\n|---|---|\n"
        f"| {_FILE} | whole |\n"
        "```\nVERDICT: REQUEST_CHANGES\n```\n"
    )
    verdict, coverage_refs = harvest_promoted_verdict_with_coverage(body)
    assert verdict == f"VERDICT: APPROVE range={_SHA}..{_SHA} head={_SHA}"
    assert coverage_refs == [
        "coverage_ref: cortex://notes/tmp/cov.json#sha256=abc123"
    ]


def test_whole_file_credit_rejects_truncated_export() -> None:
    row = _row()
    gate = evaluate_positive_whole_file_gate(
        row,
        reviewing_worker_id=_WORKER,
        server_file_sha256=_SHA,
        receipt_bytes=_RECEIPT,
        export_byte_length=40,
        file_length=120,
    )
    assert gate.passed is False
    assert gate.reason == "truncated_export"


def test_positive_gate_rejects_missing_and_wrong_reader() -> None:
    missing = evaluate_positive_whole_file_gate(
        _row(receipt_digest=""),
        reviewing_worker_id=_WORKER,
        server_file_sha256=_SHA,
        receipt_bytes=None,
        export_byte_length=120,
        file_length=120,
    )
    assert missing.passed is False
    wrong = evaluate_positive_whole_file_gate(
        _row(actual_reader="cursor-sdk:99999"),
        reviewing_worker_id=_WORKER,
        server_file_sha256=_SHA,
        receipt_bytes=_RECEIPT,
        export_byte_length=120,
        file_length=120,
    )
    assert wrong.passed is False
    assert wrong.reason == "wrong_actual_reader"
    ok = evaluate_positive_whole_file_gate(
        _row(),
        reviewing_worker_id=_WORKER,
        server_file_sha256=_SHA,
        receipt_bytes=_RECEIPT,
        export_byte_length=120,
        file_length=120,
    )
    assert ok.passed is True


def test_compact_envelope_exposes_coverage_ref() -> None:
    manifest = _manifest(
        coverage_sidecar_uri="cortex://notes/tmp/coverage/foo.json",
        coverage_sidecar_digest="deadbeef",
    )
    line = compact_envelope_coverage_ref(manifest)
    assert line.startswith("coverage_ref:")
    uri, digest = parse_coverage_ref_line(line)
    assert uri == "cortex://notes/tmp/coverage/foo.json"
    assert digest == "deadbeef"


def test_extent_enum_and_unauthorized_diff() -> None:
    assert validate_extent_authorized(
        actual_extent="allowed_diff",
        required_extent="allowed_diff",
        pin=_SHA,
        reviewed_pin=_SHA,
        diff_authorized=True,
    )
    assert not validate_extent_authorized(
        actual_extent="allowed_diff",
        required_extent="allowed_diff",
        pin="b" * 64,
        reviewed_pin=_SHA,
        diff_authorized=True,
    )
    assert not validate_extent_authorized(
        actual_extent="allowed_diff",
        required_extent="allowed_diff",
        pin=_SHA,
        reviewed_pin=_SHA,
        diff_authorized=False,
    )
    assert validate_extent_authorized(
        actual_extent="named_sections",
        required_extent="named_sections",
        pin=_SHA,
        reviewed_pin=_SHA,
        diff_authorized=False,
    )


def test_blocking_finding_survives_incomplete_coverage() -> None:
    manifest = _manifest(
        blocking_findings=["path:1 missing auth"],
        targets=[_row(actual_extent="partial", receipt_digest="")],
        measurement_owner="agent-bus:24863",
    )
    blocks, owner = blocking_survives_incomplete_coverage(manifest)
    assert blocks is True
    assert owner == "agent-bus:24863"


def test_legacy_verdict_unverified_under_new_gate() -> None:
    gate = legacy_verdict_without_coverage(
        f"VERDICT: APPROVE range={_SHA}..{_SHA} head={_SHA}"
    )
    assert gate.passed is False
    assert gate.legacy_unverified is True


def test_envelope_summary_not_authority() -> None:
    digest = hashlib.sha256(b"coordinator restatement").hexdigest()
    manifest = _manifest(targets=[_row(receipt_digest="")])
    gate = evaluate_envelope_receipt_authority(
        manifest=manifest,
        coordinator_restatement_digest=digest,
    )
    assert gate.passed is False


def test_event_vocabulary_row_present() -> None:
    row = event_vocabulary_row()
    assert row["signal"] == REVIEW_COVERAGE_GATE_EVENT
    assert row["action"] == REVIEW_COVERAGE_GATE_EVENT_ACTION
