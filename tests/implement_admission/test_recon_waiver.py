"""Unit tests for parse_recon_waiver and waiver validation helpers."""

from __future__ import annotations

import json

import pytest

from implement_admission.recon_waiver import (
    WaiverInfo,
    build_structured_waiver,
    parse_recon_waiver,
    recon_waived_bool,
    validate_recon_waive_reason_code,
)


@pytest.mark.offline
def test_parse_none_or_empty_returns_none() -> None:
    assert parse_recon_waiver(None) is None
    assert parse_recon_waiver("") is None
    assert parse_recon_waiver("   ") is None
    assert recon_waived_bool(None) is False


@pytest.mark.offline
def test_parse_legacy_bare_string_waived_reason_none() -> None:
    info = parse_recon_waiver("operator waived axis-2 for this arc")
    assert info is not None
    assert info.waived is True
    assert info.reason_code is None
    assert info.reason is None
    assert recon_waived_bool("legacy waiver") is True


@pytest.mark.offline
def test_parse_structured_json_fields() -> None:
    raw = json.dumps(
        {
            "waived_by": "claude-cursor",
            "reason_code": "operator_directive",
            "reason": "spec amended post-skeptic",
            "spec_sha256": "spec_sha256:abc",
            "waived_at": "2026-07-06T00:00:00+00:00",
        }
    )
    info = parse_recon_waiver(raw)
    assert info == WaiverInfo(
        waived=True,
        waived_by="claude-cursor",
        reason_code="operator_directive",
        reason="spec amended post-skeptic",
        spec_sha256="spec_sha256:abc",
        waived_at="2026-07-06T00:00:00+00:00",
    )
    sibling = info.to_gate_sibling()
    assert sibling is not None
    assert sibling["reason_code"] == "operator_directive"


@pytest.mark.offline
def test_parse_malformed_json_waived_reason_none_never_raises() -> None:
    info = parse_recon_waiver("{not-json")
    assert info is not None
    assert info.waived is True
    assert info.reason_code is None


@pytest.mark.offline
def test_validate_unknown_reason_code_fail_closed() -> None:
    err = validate_recon_waive_reason_code("not_a_real_code")
    assert err == {
        "error": "unknown recon_waive_reason_code: 'not_a_real_code'",
        "code": "recon_waive_reason_code_unknown",
    }


@pytest.mark.offline
def test_validate_known_reason_code_accepted() -> None:
    assert validate_recon_waive_reason_code("operator_directive") is None
    assert validate_recon_waive_reason_code(None) is None
    assert validate_recon_waive_reason_code("") is None


@pytest.mark.offline
def test_waiver_equivalence_ignores_waived_at() -> None:
    left = build_structured_waiver(
        reason_code="operator_directive",
        reason="same",
        waived_by="agent",
        spec_sha256="spec_sha256:1",
        waived_at="2026-07-06T01:00:00+00:00",
    )
    right = build_structured_waiver(
        reason_code="operator_directive",
        reason="same",
        waived_by="agent",
        spec_sha256="spec_sha256:1",
        waived_at="2026-07-06T02:00:00+00:00",
    )
    assert left.equivalent_to(right)
