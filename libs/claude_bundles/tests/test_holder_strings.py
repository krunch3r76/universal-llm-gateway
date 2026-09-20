"""Round-trip tests for cowork_cse holder string formatter/parser."""

from __future__ import annotations

import pytest
from claude_bundles.holder_strings import (
    format_cowork_cse_holder,
    format_nest_under_cse,
    holder_id_from_chat_url,
    parse_cowork_cse_holder,
    parse_nest_under_cse,
)

_CSE_URL = "https://claude.ai/cowork/cse_abc123"
_HID = "cse_abc123"


def test_holder_id_from_chat_url() -> None:
    assert holder_id_from_chat_url(_CSE_URL) == _HID


def test_cowork_cse_round_trip() -> None:
    raw = format_cowork_cse_holder(_HID)
    assert raw == "cowork_cse:cse_abc123"
    assert parse_cowork_cse_holder(raw) == _HID


def test_nest_under_cse_round_trip() -> None:
    raw = format_nest_under_cse(_HID)
    assert raw == "cse:cse_abc123"
    assert parse_nest_under_cse(raw) == _HID


def test_cowork_cse_never_parses_sdk_epoch() -> None:
    assert parse_cowork_cse_holder("cowork_cse:1234567890123") is None
    assert parse_nest_under_cse("cse:1234567890123") is None


def test_non_cse_strings_return_none() -> None:
    assert parse_cowork_cse_holder("d75f84f73338") is None
    assert parse_nest_under_cse("d75f84f73338") is None


@pytest.mark.parametrize(
    "raw",
    ["", "sdk:foo", "cse:", "cowork_cse:"],
)
def test_empty_or_malformed(raw: str) -> None:
    assert parse_cowork_cse_holder(raw) is None
    assert parse_nest_under_cse(raw) is None
