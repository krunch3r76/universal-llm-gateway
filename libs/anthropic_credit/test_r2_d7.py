"""R2 pre-land review fixes (D7 dispatch)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

import pytest
from event_store.model_rate_table import ModelRateRow, resolve_pinned_rate

from anthropic_credit.config import CreditConfigInvalid, load_credit_config
from anthropic_credit.policy import Admit, CreditPolicy, Refuse
from anthropic_credit.reservation import ReservationRefused, worst_case_usd
from anthropic_credit.test_ledger_policy import (
    _body_1_usd_on_sonnet,
    _make_cfg,
    _seed_committed_usd,
)
from anthropic_credit.usage_cost import usage_cost_usd_for_config

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


def test_server_tool_fees_not_scaled_by_tokens_per_million() -> None:
    """Break covered: web-search USD fees divided by 1e6 with token rates."""
    row = resolve_pinned_rate("anthropic/claude-sonnet-5")
    assert row is not None
    cfg = load_credit_config()
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 500,
        "server_tool_use": {"web_search_requests": 10},
    }
    in_rate = Decimal(str(row.input_rate_per_m))
    out_rate = Decimal(str(row.output_rate_per_m))
    token_part = (
        Decimal(1000) * in_rate + Decimal(500) * out_rate
    ) / Decimal("1000000")
    fee_part = Decimal(10) * cfg.server_tool_fees_usd["web_search_requests"]
    expected = token_part + fee_part
    cost, missing, unknown = usage_cost_usd_for_config(usage, row, cfg)
    assert missing == ()
    assert unknown is False
    assert cost == expected
    assert cost == Decimal("0.107")


def test_unknown_server_tool_commits_max_and_flags_partial(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(
        cfg, emit_fn=lambda name, payload: events.append((name, payload))
    )
    assert isinstance(
        policy.admit(
            "unknown-tool",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC),
        ),
        Admit,
    )
    committed = policy.commit(
        "unknown-tool",
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100,
            "server_tool_use": {"unknown_fee_key": 3},
        },
        now=datetime(2026, 10, 17, 15, 1, 0, tzinfo=UTC),
    )
    assert committed == Decimal("1.000000")
    assert any(name == "anthropic.credit.usage_partial" for name, _ in events)


def test_open_reserve_counts_in_window_after_roll(tmp_path: Path) -> None:
    """Break covered: open reserve at window roll omitted from new window open total."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    seed_time = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    _seed_committed_usd(policy, Decimal("184"), now=seed_time)
    pre_roll = datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "roll-reserve",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=pre_roll,
        ),
        Admit,
    )
    post_roll = datetime(2026, 11, 6, 0, 0, 1, tzinfo=UTC)
    second = policy.admit(
        "post-roll",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=post_roll,
    )
    assert isinstance(second, Refuse)
    assert second.code == "anthropic_cycle_budget_exhausted"


def test_mcp_serialization_across_window_roll(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    mcp_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "vortex"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    pre_roll = datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "mcp-open",
            "anthropic/claude-sonnet-5",
            mcp_body,
            token_count=100,
            now=pre_roll,
        ),
        Admit,
    )
    post_roll = datetime(2026, 11, 6, 0, 0, 1, tzinfo=UTC)
    second = policy.admit(
        "mcp-second",
        "anthropic/claude-sonnet-5",
        mcp_body,
        token_count=100,
        now=post_roll,
    )
    assert isinstance(second, Refuse)
    assert "serialized" in (second.detail or "").lower()


def test_tagged_ttl_commit_then_real_commit_overrun_and_doubles_mcp(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(
        cfg, emit_fn=lambda name, payload: events.append((name, payload))
    )
    mcp_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "vortex"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "tagged-req",
            "anthropic/claude-sonnet-5",
            mcp_body,
            tag="workbench-session",
            token_count=100,
            now=t0,
        ),
        Admit,
    )
    ttl_at = t0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    policy.commit(
        "tagged-req",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 600_000,
        },
        now=ttl_at + timedelta(seconds=1),
    )
    assert any(name == "anthropic.credit.overrun" for name, _ in events)
    mcp_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "vortex"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    res_mcp = policy.admit(
        "mcp-after-overrun",
        "anthropic/claude-sonnet-5",
        mcp_body,
        token_count=100,
        now=ttl_at + timedelta(minutes=2),
    )
    assert isinstance(res_mcp, Admit)
    assert res_mcp.reservation_usd == Decimal("10.000000")


def test_workbench_mcp_finalize_unblocks_lane_after_ttl(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    mcp_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 50_000,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "workbench"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "wb-mcp",
            "anthropic/claude-sonnet-5",
            mcp_body,
            token_count=100,
            now=t0,
        ),
        Admit,
    )
    ttl_at = t0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    policy.commit(
        "wb-mcp",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100_000,
        },
        now=ttl_at + timedelta(seconds=1),
    )
    st = policy.status(now=ttl_at + timedelta(minutes=1))
    assert st["open_mcp_reservations"] == 0


def test_mcp_static_input_uses_2x_without_cache_control() -> None:
    cfg = dataclasses.replace(
        load_credit_config(), mcp_connector_allowance_usd=Decimal("0.001")
    )
    row = _sonnet_row()
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 100,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "m"}
        ],
        "messages": [{"role": "user", "content": "x"}],
    }
    res = worst_case_usd(body, row, token_count=200, cfg=cfg)
    in_rate = Decimal("2.0") * Decimal("2.0")
    out_rate = Decimal("10.0")
    expected = (
        Decimal(220) * in_rate + Decimal(100) * out_rate
    ) / Decimal("1000000")
    expected = expected.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
    assert res.usd == max(Decimal("0.001"), expected)


def test_inline_text_and_content_document_sources_allowed() -> None:
    cfg = load_credit_config()
    row = _sonnet_row()
    for src_type in ("text", "content"):
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
                                "type": src_type,
                                "media_type": "text/plain",
                                "data": "hello",
                            },
                        }
                    ],
                }
            ],
        }
        res = worst_case_usd(body, row, token_count=None, cfg=cfg)
        assert res.basis == "byte_fallback"


def test_non_inline_url_still_refused() -> None:
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
                        "type": "image",
                        "source": {"type": "url", "url": "https://example.com/x.png"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(ReservationRefused, match="non-inline"):
        worst_case_usd(body, row, token_count=None, cfg=cfg)


@pytest.mark.parametrize(
    "override,match",
    [
        ({"mcp_connector_allowance_usd": 0}, "mcp_connector_allowance_usd"),
        ({"reservation_ttl_minutes": 0}, "reservation_ttl_minutes"),
        ({"fail_mode": "open"}, "fail_mode"),
        ({"count_tokens_margin": "0.5"}, "count_tokens_margin"),
        (
            {"server_tool_fees_usd": {"web_search_requests": -0.01}},
            "server_tool_fees_usd",
        ),
    ],
)
def test_f8_config_validators(tmp_path: Path, override: dict, match: str) -> None:
    from anthropic_credit.test_config import _write_minimal_config

    path = _write_minimal_config(tmp_path, **override)
    with pytest.raises(CreditConfigInvalid, match=match):
        load_credit_config(path)
