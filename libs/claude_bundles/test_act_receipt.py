"""Unit tests for ACT-RECEIPT grammar."""

from __future__ import annotations

import pytest

from claude_bundles.act_receipt import (
    SHIPPED_COMMISSION_KINDS,
    format_act_receipt,
    parse_act_receipt,
)

pytestmark = pytest.mark.offline


def test_format_and_parse_round_trip() -> None:
    for kind in SHIPPED_COMMISSION_KINDS:
        text = format_act_receipt(
            commission_kind=kind,
            evidence_uri="cortex://notes/system/ephemeral/x.md",
            trigger_id="tid-1",
            execution_id="exec-1",
        )
        parsed = parse_act_receipt(text)
        assert parsed is not None
        assert parsed.commission_kind == kind
        assert parsed.evidence_uri == "cortex://notes/system/ephemeral/x.md"
        assert parsed.trigger_id == "tid-1"
        assert parsed.execution_id == "exec-1"


def test_parse_raw_json_fallback() -> None:
    raw = (
        '{"act_receipt":true,"commission_kind":"charter_enroll",'
        '"evidence_uri":"cortex://notes/system/threads/6237"}'
    )
    parsed = parse_act_receipt(raw)
    assert parsed is not None
    assert parsed.commission_kind == "charter_enroll"


def test_reject_nested_sdk_and_unknown_kind() -> None:
    assert (
        parse_act_receipt(
            '{"act_receipt":true,"commission_kind":"nested_sdk","evidence_uri":"x"}'
        )
        is None
    )
    assert parse_act_receipt('{"act_receipt":true,"commission_kind":"bogus","evidence_uri":"x"}') is None
    with pytest.raises(ValueError):
        format_act_receipt(commission_kind="nested_sdk", evidence_uri="x")


def test_reject_malformed() -> None:
    assert parse_act_receipt("") is None
    assert parse_act_receipt("not json") is None
    assert parse_act_receipt('{"act_receipt":false,"commission_kind":"agent_bus_request","evidence_uri":"x"}') is None
    assert parse_act_receipt('{"act_receipt":true,"commission_kind":"agent_bus_request"}') is None
