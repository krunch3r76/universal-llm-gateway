"""R3 review fixes (D8 dispatch)."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from anthropic_credit.config import AvailableBalanceConfig, load_credit_config
from anthropic_credit.ledger import CreditLedger, LedgerUnavailable
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy
from anthropic_credit.test_ledger_policy import _body_1_usd_on_sonnet, _make_cfg

pytestmark = pytest.mark.offline


def test_commit_full_after_ttl_only_inserts_zero_finalize(tmp_path: Path) -> None:
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
            "mcp-ttl-full",
            "anthropic/claude-sonnet-5",
            mcp_body,
            token_count=100,
            now=t0,
        ),
        Admit,
    )
    ttl_at = t0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    policy.commit_full("mcp-ttl-full", "upstream_http_500", now=ttl_at + timedelta(seconds=1))
    st = policy.status(now=ttl_at + timedelta(minutes=1))
    assert st["open_mcp_reservations"] == 0
    commits = [e for e in st["entries"] if e["kind"] == "commit"]
    assert any(Decimal(str(c["usd"])) == Decimal("0") for c in commits)


def test_first_zero_usage_commit_writes_finalize_row(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "zero-commit",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.commit(
        "zero-commit",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
        },
        now=t0,
    )
    commits = [e for e in policy.status()["entries"] if e["kind"] == "commit"]
    assert len(commits) == 1
    assert Decimal(str(commits[0]["usd"])) == Decimal("0")


def test_spend_since_as_of_balance_cap_uses_post_as_of_spend_only(tmp_path: Path) -> None:
    cfg = _make_cfg(
        tmp_path,
        available_balance=AvailableBalanceConfig(
            usd=Decimal("2.00"),
            as_of="2026-09-20T00:00:00+00:00",
            owner="kaywan",
        ),
    )
    policy = CreditPolicy(cfg)
    t_prev = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "prev-win",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=t_prev,
        ),
        Admit,
    )
    policy.commit(
        "prev-win",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100_000,
        },
        now=t_prev,
    )
    t_now = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    st = policy.status(now=t_now)
    assert st["balance_cap_usd"] == Decimal("1")
    first = policy.admit(
        "cap-one",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=t_now,
    )
    assert isinstance(first, Admit)
    policy.commit(
        "cap-one",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100_000,
        },
        now=t_now,
    )
    second = policy.admit(
        "cap-two",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=t_now,
    )
    assert not isinstance(second, Admit)


def test_ledger_schema_mismatch_refused(tmp_path: Path) -> None:
    ledger_path = tmp_path / "bad_schema.sqlite"
    cfg = dataclasses.replace(load_credit_config(), ledger_path=ledger_path)
    init_ledger(cfg)
    with sqlite3.connect(str(ledger_path)) as conn:
        conn.execute(
            "UPDATE meta SET value = '1' WHERE key = 'schema_version'"
        )
    ledger = CreditLedger(ledger_path, create_if_missing=False)
    with pytest.raises(LedgerUnavailable, match="schema_mismatch"):
        ledger.run_immediate(lambda conn: conn.execute("SELECT 1").fetchone())
