"""Tests for credit refusal parsing and loop cap notices."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from anthropic_credit.loop_refusal import (
    credit_cap_notice,
    parse_credit_refusal_detail,
    parse_credit_refusal_type,
)
from anthropic_credit.policy import AnthropicCreditRefused, Refuse


def test_credit_cap_notice_zero_arg_unchanged() -> None:
    assert credit_cap_notice() == (
        "[Anthropic credit cycle budget exhausted] Further model turns were "
        "stopped to stay within the configured spend cap. Partial results above "
        "are preserved."
    )


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("anthropic_credit_latched", "credit latch active"),
        (
            "anthropic_mcp_serialized",
            "MCP connector requests are serialized",
        ),
        ("anthropic_mcp_cap_exceeded", "MCP connector spend cap exceeded"),
        ("anthropic_cycle_budget_exhausted", "credit cycle budget exhausted"),
    ],
)
def test_credit_cap_notice_per_code(code: str, fragment: str) -> None:
    notice = credit_cap_notice(code=code, detail="extra-detail")
    assert fragment in notice
    assert f"code={code}" in notice
    assert "extra-detail" in notice


def test_parse_credit_refusal_detail_from_refused() -> None:
    exc = AnthropicCreditRefused(
        Refuse(
            code="anthropic_mcp_serialized",
            http_status=429,
            reservation_usd=Decimal("1"),
            headroom_usd=None,
            detail="1 MCP reservation(s) open",
        )
    )
    assert parse_credit_refusal_type(exc) == "anthropic_mcp_serialized"
    assert parse_credit_refusal_detail(exc) == "1 MCP reservation(s) open"


def test_parse_credit_refusal_detail_from_http_message() -> None:
    exc = httpx.HTTPStatusError(
        "refused",
        request=httpx.Request("POST", "http://test"),
        response=httpx.Response(
            429,
            json={
                "error": {
                    "type": "anthropic_credit_latched",
                    "message": "latch reason text",
                }
            },
        ),
    )
    assert parse_credit_refusal_type(exc) == "anthropic_credit_latched"
    assert parse_credit_refusal_detail(exc) == "latch reason text"
