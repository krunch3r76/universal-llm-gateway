"""Tests for worst-case USD reservation sizing (leg A2)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from event_store.model_rate_table import ModelRateRow

from anthropic_credit.config import load_credit_config
from anthropic_credit.reservation import ReservationRefused, worst_case_usd

pytestmark = pytest.mark.offline


def _sonnet_row() -> ModelRateRow:
    return ModelRateRow(
        model_id="anthropic/claude-sonnet-5",
        input_rate_per_m=2.0,
        output_rate_per_m=10.0,
        cache_write_rate_per_m=2.5,
        cache_read_rate_per_m=0.2,
        source="manual_seed",
        updated_at="2026-10-17T00:00:00+00:00",
        pinned=True,
    )


def _haiku_long_row() -> ModelRateRow:
    return ModelRateRow(
        model_id="anthropic/claude-haiku-5-5",
        input_rate_per_m=0.10,
        output_rate_per_m=0.50,
        cache_write_rate_per_m=0.125,
        cache_read_rate_per_m=0.01,
        long_prompt_threshold_tokens=100_000,
        long_input_rate_per_m=0.50,
        long_cache_write_rate_per_m=0.625,
        long_cache_read_rate_per_m=0.05,
        long_output_rate_per_m=2.50,
        source="manual_seed",
        updated_at="2026-10-17T00:00:00+00:00",
        pinned=True,
    )


@pytest.mark.parametrize("bad_max_tokens", [None, 0, -10, True, "100"])
def test_missing_or_non_positive_max_tokens_refused(bad_max_tokens: Any) -> None:
    """Break covered: unbounded output tokens admitted when max_tokens is missing/invalid."""
    cfg = load_credit_config()
    body: dict[str, Any] = {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    if bad_max_tokens is not None:
        body["max_tokens"] = bad_max_tokens
    with pytest.raises(ReservationRefused, match="max_tokens") as exc_info:
        worst_case_usd(body, _sonnet_row(), token_count=100, cfg=cfg)
    assert exc_info.value.code == "anthropic_reservation_refused"


def test_fast_speed_refused() -> None:
    """Break covered: unpriced speed='fast' multiplier bypassing reservation upper bound."""
    cfg = load_credit_config()
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 512,
        "speed": "fast",
        "messages": [{"role": "user", "content": "hi"}],
    }
    with pytest.raises(ReservationRefused, match="fast") as exc_info:
        worst_case_usd(body, _sonnet_row(), token_count=100, cfg=cfg)
    assert exc_info.value.code == "anthropic_reservation_refused"


def test_cache_control_doubles_input_rate_and_absence_does_not() -> None:
    """Break covered: 1h cache-write (2x input) under-reserved when cache_control is set, or over-reserved when absent."""
    cfg = load_credit_config()
    row = _sonnet_row()  # input=2.0/M, output=10.0/M

    plain_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "hello"}],
    }
    res_plain = worst_case_usd(plain_body, row, token_count=1000, cfg=cfg)
    # input_bound = ceil(1000 * 1.10) = 1100; 1100 * $2/M + 1000 * $10/M = 0.0022 + 0.0100 = 0.012200
    assert res_plain.input_tokens_bound == 1100
    assert res_plain.output_tokens_bound == 1000
    assert res_plain.mcp_class is False
    assert res_plain.usd == Decimal("0.012200")

    # Top-level cache_control
    top_cc = {**plain_body, "cache_control": {"type": "ephemeral"}}
    res_top = worst_case_usd(top_cc, row, token_count=1000, cfg=cfg)
    # 1100 * $4/M + 1000 * $10/M = 0.0044 + 0.0100 = 0.014400
    assert res_top.usd == Decimal("0.014400")

    # System block cache_control
    sys_cc = {
        **plain_body,
        "system": [
            {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}
        ],
    }
    assert worst_case_usd(sys_cc, row, token_count=1000, cfg=cfg).usd == Decimal(
        "0.014400"
    )

    # Message content block cache_control
    msg_cc = {
        "model": "claude-sonnet-5",
        "max_tokens": 1000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hi",
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            }
        ],
    }
    assert worst_case_usd(msg_cc, row, token_count=1000, cfg=cfg).usd == Decimal(
        "0.014400"
    )

    # Tool block cache_control vs plain custom tool without cache_control
    tool_plain = {
        **plain_body,
        "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
    }
    assert worst_case_usd(tool_plain, row, token_count=1000, cfg=cfg).usd == Decimal(
        "0.012200"
    )

    tool_cc = {
        **plain_body,
        "tools": [
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    assert worst_case_usd(tool_cc, row, token_count=1000, cfg=cfg).usd == Decimal(
        "0.014400"
    )


def test_long_tier_at_threshold_plus_one() -> None:
    """Break covered: >100k prompt priced at base tier instead of 5x long tier."""
    cfg = load_credit_config()
    # Set margin to 1.0 so token_count maps 1:1 to input_tokens_bound for boundary test
    import dataclasses

    cfg_exact = dataclasses.replace(cfg, count_tokens_margin=Decimal("1.0"))
    row = _haiku_long_row()
    body = {
        "model": "claude-haiku-5-5",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "x"}],
    }

    at_threshold = worst_case_usd(body, row, token_count=100_000, cfg=cfg_exact)
    # 100,000 * 0.10/M + 1,000 * 0.50/M = 0.01 + 0.0005 = 0.010500
    assert at_threshold.input_tokens_bound == 100_000
    assert at_threshold.usd == Decimal("0.010500")

    over_threshold = worst_case_usd(body, row, token_count=100_001, cfg=cfg_exact)
    # 100,001 * 0.50/M + 1,000 * 2.50/M = 0.0500005 + 0.0025 = 0.0525005 -> ceil to 1e-6 -> 0.052501
    assert over_threshold.input_tokens_bound == 100_001
    assert over_threshold.usd == Decimal("0.052501")


def test_mcp_class_gets_fixed_allowance() -> None:
    """Break covered: server-side MCP/web_search loop sized from initial body instead of $5 allowance."""
    cfg = load_credit_config()
    row = _sonnet_row()

    body_mcp_servers = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "vortex"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    res_mcp = worst_case_usd(body_mcp_servers, row, token_count=200, cfg=cfg)
    assert res_mcp.mcp_class is True
    assert res_mcp.usd == Decimal("5.000000")
    assert res_mcp.basis == "mcp_max_allowance_static"

    body_server_tool = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "tools": [{"type": "web_search_20260209", "name": "web_search"}],
        "messages": [{"role": "user", "content": "search"}],
    }
    res_tool = worst_case_usd(body_server_tool, row, token_count=200, cfg=cfg)
    assert res_tool.mcp_class is True
    assert res_tool.usd == Decimal("5.000000")


def test_byte_fallback_exceeds_plausible_token_count() -> None:
    """Break covered: count_tokens failure falling back to an underestimate of input tokens."""
    cfg = load_credit_config()
    row = _sonnet_row()

    representative_bodies: list[tuple[dict[str, Any], int]] = [
        (
            {
                "model": "claude-sonnet-5",
                "max_tokens": 512,
                "system": "You are a concise assistant.",
                "messages": [
                    {"role": "user", "content": "Summarize the key points." * 40}
                ],
            },
            0,
        ),
        (
            {
                "model": "claude-sonnet-5",
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Inspect this image and document.",
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "url",
                                    "url": "https://example.com/a.png",
                                },
                            },
                            {
                                "type": "document",
                                "source": {
                                    "type": "text",
                                    "media_type": "text/plain",
                                    "data": "abc",
                                },
                            },
                        ],
                    }
                ],
            },
            2,
        ),
    ]

    body_plain, _media_count = representative_bodies[0]
    raw_bytes = len(json.dumps(body_plain).encode("utf-8"))
    plausible_tokens = int(raw_bytes / 3.5)
    res_counted = worst_case_usd(body_plain, row, token_count=plausible_tokens, cfg=cfg)
    res_fallback = worst_case_usd(body_plain, row, token_count=None, cfg=cfg)
    assert res_fallback.basis == "byte_fallback"
    assert res_fallback.input_tokens_bound >= res_counted.input_tokens_bound
    assert res_fallback.usd >= res_counted.usd

    body_url_media = representative_bodies[1][0]
    with pytest.raises(ReservationRefused, match="non-inline"):
        worst_case_usd(body_url_media, row, token_count=None, cfg=cfg)


def test_base64_inline_document_allowed() -> None:
    cfg = load_credit_config()
    row = _sonnet_row()
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 512,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "JVBERi0x",
                        },
                    }
                ],
            }
        ],
    }
    res = worst_case_usd(body, row, token_count=None, cfg=cfg)
    assert res.basis == "byte_fallback"
