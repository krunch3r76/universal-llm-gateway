"""Tests for CDP AMEND review items (F1–F4, F12–F14)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from event_store.model_rate_table import ModelRateRow

from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger import validate_ledger_path
from anthropic_credit.ledger_init import LedgerAlreadyExists, init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, Refuse
from anthropic_credit.reservation import worst_case_usd

pytestmark = pytest.mark.offline

_T0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)


def _cfg(tmp_path: Path) -> CreditPolicy:
    base = load_credit_config()
    cfg = dataclasses.replace(
        base,
        ledger_path=tmp_path / "ledger.sqlite",
    )
    init_ledger(cfg)
    return CreditPolicy(cfg)


def test_duplicate_request_id_refused(tmp_path: Path) -> None:
    policy = _cfg(tmp_path)
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "x"}],
    }
    first = policy.admit("dup-req", "anthropic/claude-sonnet-5", body, token_count=0, now=_T0)
    assert isinstance(first, Admit)
    second = policy.admit("dup-req", "anthropic/claude-sonnet-5", body, token_count=0, now=_T0)
    assert isinstance(second, Refuse)
    assert second.code == "anthropic_reservation_refused"


def test_missing_ledger_refuses_until_init(tmp_path: Path) -> None:
    base = load_credit_config()
    cfg = dataclasses.replace(base, ledger_path=tmp_path / "missing.sqlite")
    policy = CreditPolicy(cfg)
    res = policy.admit(
        "r1",
        "anthropic/claude-sonnet-5",
        {"model": "claude-sonnet-5", "max_tokens": 100, "messages": []},
        token_count=0,
        now=_T0,
    )
    assert isinstance(res, Refuse)
    assert res.code == "anthropic_credit_gate_unavailable"
    init_ledger(cfg)
    policy2 = CreditPolicy(cfg)
    res2 = policy2.admit(
        "r2",
        "anthropic/claude-sonnet-5",
        {"model": "claude-sonnet-5", "max_tokens": 100, "messages": []},
        token_count=0,
        now=_T0,
    )
    assert isinstance(res2, Admit)
    with pytest.raises(LedgerAlreadyExists):
        init_ledger(cfg)


def test_fable_mcp_reservation_at_least_640_usd() -> None:
    cfg = load_credit_config()
    row = ModelRateRow(
        model_id="anthropic/claude-fable-5-1",
        input_rate_per_m=10.0,
        output_rate_per_m=50.0,
        cache_write_rate_per_m=12.5,
        cache_read_rate_per_m=0.25,
        source="manual_seed",
        updated_at="2026-10-17T00:00:00+00:00",
        pinned=True,
    )
    body = {
        "model": "claude-fable-5-1",
        "max_tokens": 128_000,
        "mcp_servers": [{"type": "url", "url": "https://example.com/mcp", "name": "m"}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    res = worst_case_usd(body, row, token_count=None, cfg=cfg)
    assert res.mcp_class is True
    assert res.usd >= Decimal("6.40")


def test_ttl_then_late_commit_top_up_and_overrun(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []
    cfg = dataclasses.replace(
        load_credit_config(), ledger_path=tmp_path / "ledger.sqlite"
    )
    init_ledger(cfg)
    policy = CreditPolicy(
        cfg, emit_fn=lambda name, payload: events.append((name, payload))
    )
    body = {
        "model": "claude-sonnet-5",
        "max_tokens": 100_000,
        "messages": [{"role": "user", "content": "x"}],
    }
    assert isinstance(
        policy.admit("ttl-req", "anthropic/claude-sonnet-5", body, token_count=0, now=_T0),
        Admit,
    )
    ttl_at = _T0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    late = policy.commit(
        "ttl-req",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 150_000,
        },
        now=ttl_at + timedelta(seconds=1),
    )
    assert late > Decimal("1.0")
    assert any(name == "anthropic.credit.overrun" for name, _ in events)
    st = policy.status(now=ttl_at + timedelta(minutes=1))
    assert st["committed_usd"] == late


def test_nfs_mount_refused(tmp_path: Path) -> None:
    path = (tmp_path / "ledger.sqlite").resolve()
    mounts = [(str(tmp_path), "nfs4")]
    with pytest.raises(Exception, match="refused filesystem"):
        validate_ledger_path(path, mounts_table=mounts)


def test_internal_mutating_routes_removed() -> None:
    from services.universal_cloud_proxy import cloud_proxy as cp

    paths = {getattr(r, "path", None) for r in cp.app.routes}
    assert "/internal/anthropic-credit/status" in paths
    assert "/internal/anthropic-credit/reserve" not in paths
    assert "/internal/anthropic-credit/commit" not in paths
    assert "/internal/anthropic-credit/release" not in paths
