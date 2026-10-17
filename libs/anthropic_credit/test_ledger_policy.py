"""Tests for SQLite ledger and admission/commit policy (leg A3)."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import multiprocessing
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from anthropic_credit.config import (
    AvailableBalanceConfig,
    CreditConfig,
    load_credit_config,
)
from anthropic_credit.ledger import CreditLedger, LedgerUnavailable
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, Refuse

pytestmark = pytest.mark.offline

_T0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)


def _make_cfg(tmp_path: Path, **overrides: Any) -> CreditConfig:
    base = load_credit_config()
    ledger_path = tmp_path / "state" / "anthropic_credit.sqlite"
    replaced = dataclasses.replace(base, ledger_path=ledger_path, **overrides)
    init_ledger(replaced)
    return replaced


def _body_1_usd_on_sonnet() -> dict[str, Any]:
    """On sonnet-5 (input=$2/M, output=$10/M), token_count=0 and max_tokens=100,000 => $1.00."""
    return {
        "model": "claude-sonnet-5",
        "max_tokens": 100_000,
        "messages": [{"role": "user", "content": "x"}],
    }


def _seed_committed_usd(
    policy: CreditPolicy, amount_usd: Decimal, now: datetime = _T0
) -> None:
    """Seed an exact committed spend in the current window without triggering overrun."""
    # Sonnet-5 output is $10/M => 100,000 output tokens = $1.00
    output_tokens = int(amount_usd * Decimal(100_000))
    res = policy.admit(
        "seed-req",
        "anthropic/claude-sonnet-5",
        {
            "model": "claude-sonnet-5",
            "max_tokens": max(1, output_tokens),
            "messages": [{"role": "user", "content": "seed"}],
        },
        token_count=0,
        now=now,
    )
    assert isinstance(res, Admit)
    committed = policy.commit(
        "seed-req",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": output_tokens,
        },
        now=now,
    )
    assert committed == amount_usd


def _mp_worker_admit(args: tuple[str, str, int]) -> bool:
    """Subprocess worker for multiprocess concurrency test against one SQLite file."""
    db_path_str, req_id, index = args
    _ = index
    base = load_credit_config()
    cfg = dataclasses.replace(base, ledger_path=Path(db_path_str))
    policy = CreditPolicy(cfg, busy_timeout_ms=5000)
    res = policy.admit(
        req_id,
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    return isinstance(res, Admit)


def test_eight_concurrent_admits_threads_cannot_overshoot_185(tmp_path: Path) -> None:
    """Break covered: TOCTOU race across 8 concurrent thread admits at $180 exceeding $185."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg, busy_timeout_ms=5000)
    _seed_committed_usd(policy, Decimal("180.00"))

    def _call(i: int) -> Admit | Refuse:
        return policy.admit(
            f"thr-req-{i}",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=_T0,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_call, range(8)))

    admitted = [r for r in results if isinstance(r, Admit)]
    refused = [r for r in results if isinstance(r, Refuse)]
    assert len(admitted) == 5
    assert len(refused) == 3
    for r in refused:
        assert r.code == "anthropic_cycle_budget_exhausted"
        assert r.http_status == 429

    st = policy.status(now=_T0)
    assert st["committed_usd"] == Decimal("180.00")
    assert st["open_reservations_usd"] == Decimal("5.00")
    assert st["committed_usd"] + st["open_reservations_usd"] == Decimal("185.00")
    assert st["headroom_usd"] == Decimal("0")


def test_eight_concurrent_admits_processes_cannot_overshoot_185(tmp_path: Path) -> None:
    """Break covered: cross-process SQLite race without BEGIN IMMEDIATE exceeding $185."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg, busy_timeout_ms=5000)
    _seed_committed_usd(policy, Decimal("180.00"))

    ctx = multiprocessing.get_context("spawn")
    tasks = [(str(cfg.ledger_path), f"proc-req-{i}", i) for i in range(8)]
    with ctx.Pool(processes=8) as pool:
        outcomes = pool.map(_mp_worker_admit, tasks)

    assert sum(1 for ok in outcomes if ok) == 5
    assert sum(1 for ok in outcomes if not ok) == 3

    st = policy.status(now=_T0)
    assert st["committed_usd"] + st["open_reservations_usd"] == Decimal("185.00")


def test_ledger_parent_dir_mode_0700_and_mnt_refused(tmp_path: Path) -> None:
    """Break covered: world-readable state dir or NFS /mnt/ ledger path (K38)."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    assert policy.healthy() is True
    parent_mode = cfg.ledger_path.parent.stat().st_mode & 0o777
    assert parent_mode == 0o700

    with pytest.raises(LedgerUnavailable, match="/mnt/"):
        CreditLedger("/mnt/torus/bad_ledger.sqlite")

    cfg_mnt = dataclasses.replace(cfg, ledger_path=Path("/mnt/torus/bad.sqlite"))
    policy_mnt = CreditPolicy(cfg_mnt)
    assert policy_mnt.healthy() is False
    res = policy_mnt.admit(
        "req-mnt",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res, Refuse)
    assert res.code == "anthropic_credit_gate_unavailable"
    assert res.http_status == 503


def test_ledger_missing_locked_or_corrupt_fails_closed(tmp_path: Path) -> None:
    """Break covered: missing, locked, or corrupt SQLite file admitting traffic instead of failing closed."""
    # 1. Missing file after initialization
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg, busy_timeout_ms=50)
    assert policy.healthy() is True
    cfg.ledger_path.unlink()
    assert policy.healthy() is False
    res_missing = policy.admit(
        "req-missing",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res_missing, Refuse)
    assert res_missing.code == "anthropic_credit_gate_unavailable"
    assert res_missing.http_status == 503

    # 2. Locked file beyond busy_timeout
    cfg_lock = _make_cfg(tmp_path / "locked")
    policy_lock = CreditPolicy(cfg_lock, busy_timeout_ms=50)
    lock_conn = sqlite3.connect(str(cfg_lock.ledger_path), isolation_level=None)
    try:
        lock_conn.execute("BEGIN EXCLUSIVE")
        res_locked = policy_lock.admit(
            "req-locked",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=_T0,
        )
        assert isinstance(res_locked, Refuse)
        assert res_locked.code == "anthropic_credit_gate_unavailable"
        assert res_locked.http_status == 503
        assert policy_lock.healthy() is False
    finally:
        lock_conn.execute("ROLLBACK")
        lock_conn.close()

    # 3. Corrupt file
    corrupt_dir = tmp_path / "corrupt" / "state"
    corrupt_dir.mkdir(parents=True)
    corrupt_file = corrupt_dir / "anthropic_credit.sqlite"
    corrupt_file.write_bytes(b"not a valid sqlite3 header at all")
    cfg_corrupt = dataclasses.replace(cfg, ledger_path=corrupt_file)
    policy_corrupt = CreditPolicy(cfg_corrupt, busy_timeout_ms=50)
    res_corrupt = policy_corrupt.admit(
        "req-corrupt",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res_corrupt, Refuse)
    assert res_corrupt.code == "anthropic_credit_gate_unavailable"
    assert res_corrupt.http_status == 503


def test_ttl_expiry_converts_open_reservation_to_full_commit(tmp_path: Path) -> None:
    """Break covered: leaked reservation with no commit/release never counted as committed."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)

    res = policy.admit(
        "req-ttl",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res, Admit)
    assert res.reservation_usd == Decimal("1.000000")

    # Before TTL (14 min): still open reservation
    st_before = policy.status(now=_T0 + timedelta(minutes=14))
    assert st_before["open_reservations_usd"] == Decimal("1.000000")
    assert st_before["committed_usd"] == Decimal("0")

    # At TTL (15 min): converted to full commit
    st_after = policy.status(now=_T0 + timedelta(minutes=15))
    assert st_after["open_reservations_usd"] == Decimal("0")
    assert st_after["committed_usd"] == Decimal("1.000000")


def test_latch_refuses_until_reset_at_then_clears(tmp_path: Path) -> None:
    """Break covered: retry storm hitting depleted Anthropic org before reset_at, or staying latched after reset_at."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)

    policy.latch("Your credit balance is too low to access the Anthropic API.", now=_T0)
    st = policy.status(now=_T0)
    assert st["latched"] is True

    # 1 second before reset_at (2026-11-06T00:00:00Z): still refused
    before_reset = datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)
    refused = policy.admit(
        "req-latched",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=before_reset,
    )
    assert isinstance(refused, Refuse)
    assert refused.code == "anthropic_cycle_budget_exhausted"
    assert refused.http_status == 429

    # At reset_at (2026-11-06T00:00:00Z): latch clears for the new cycle
    at_reset = datetime(2026, 11, 6, 0, 0, 0, tzinfo=UTC)
    admitted = policy.admit(
        "req-after-reset",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=at_reset,
    )
    assert isinstance(admitted, Admit)
    assert policy.status(now=at_reset)["latched"] is False


def test_commit_full_after_abort_and_release_before_upstream(tmp_path: Path) -> None:
    """Break covered: aborted stream under-committing tokens generated before disconnect."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)

    res1 = policy.admit(
        "req-abort",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res1, Admit)
    committed = policy.commit_full("req-abort", reason="stream_cancelled", now=_T0)
    assert committed == Decimal("1.000000")

    res2 = policy.admit(
        "req-release",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res2, Admit)
    policy.release("req-release", now=_T0)

    st = policy.status(now=_T0)
    assert st["committed_usd"] == Decimal("1.000000")
    assert st["open_reservations_usd"] == Decimal("0")


def test_partial_usage_commits_max_and_overrun_doubles_mcp_allowance(
    tmp_path: Path,
) -> None:
    """Break covered: partial usage committing below reservation, or overrun failing to double MCP allowance."""
    events: list[tuple[str, dict[str, Any]]] = []
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(
        cfg, emit_fn=lambda name, payload: events.append((name, payload))
    )

    # 1. Partial usage commits max(computed, reservation) and emits usage_partial
    res_p = policy.admit(
        "req-partial",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res_p, Admit)
    committed_p = policy.commit("req-partial", {"input_tokens": 100}, now=_T0)
    assert committed_p == Decimal("1.000000")
    assert any(name == "anthropic.credit.usage_partial" for name, _ in events)

    # 2. Overrun (> reservation) emits anthropic.credit.overrun and doubles MCP allowance ($5 -> $10)
    res_o = policy.admit(
        "req-overrun",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(res_o, Admit)
    committed_o = policy.commit(
        "req-overrun",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 200_000,  # $2.00 > $1.00 reservation
        },
        now=_T0,
    )
    assert committed_o == Decimal("2.0")
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
        "req-mcp-after-overrun",
        "anthropic/claude-sonnet-5",
        mcp_body,
        token_count=100,
        now=_T0,
    )
    assert isinstance(res_mcp, Admit)
    assert res_mcp.reservation_usd == Decimal("10.000000")


def test_unpriced_or_unmetered_model_refused(tmp_path: Path) -> None:
    """Break covered: unpinned/aliased or OpenRouter model admitted on Anthropic meter."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)

    for bad_model in (
        "anthropic/claude-opus-5",  # alias to openrouter in model_rates.yaml, no pinned row
        "openrouter/anthropic/claude-opus-5",  # OpenRouter provider exclusion
        "anthropic/claude-unknown-99",
    ):
        res = policy.admit(
            f"req-{bad_model}",
            bad_model,
            _body_1_usd_on_sonnet(),
            token_count=100,
            now=_T0,
        )
        assert isinstance(res, Refuse)
        assert res.code == "anthropic_model_unpriced"
        assert res.http_status == 422


def test_mcp_stop_at_170_and_serialization(tmp_path: Path) -> None:
    """Break covered: parallel MCP loops or MCP admission above $170 stop."""
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    _seed_committed_usd(policy, Decimal("165.00"))

    mcp_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "mcp_servers": [
            {"type": "url", "url": "https://example.com/mcp", "name": "vortex"}
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }

    # At $165 committed, one $5 MCP request reaches $170 -> admitted
    mcp1 = policy.admit("mcp-1", "claude-sonnet-5", mcp_body, token_count=100, now=_T0)
    assert isinstance(mcp1, Admit)
    assert mcp1.mcp_class is True
    assert mcp1.reservation_usd == Decimal("5.000000")

    # While mcp-1 is open, a second MCP request is refused (serialized + at $170 cap)
    mcp2 = policy.admit("mcp-2", "claude-sonnet-5", mcp_body, token_count=100, now=_T0)
    assert isinstance(mcp2, Refuse)
    assert mcp2.code == "anthropic_cycle_budget_exhausted"
    assert mcp2.http_status == 429

    # Release mcp-1 and commit $1.00 plain request so committed = $166.00
    policy.release("mcp-1", now=_T0)
    plain = policy.admit(
        "plain-166",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(plain, Admit)
    policy.commit_full("plain-166", reason="done", now=_T0)

    # At $166 committed, $5 MCP request would reach $171 > $170 -> refused
    mcp3 = policy.admit("mcp-3", "claude-sonnet-5", mcp_body, token_count=100, now=_T0)
    assert isinstance(mcp3, Refuse)
    assert mcp3.code == "anthropic_cycle_budget_exhausted"
    assert mcp3.headroom_usd == Decimal("4.000000")


def test_cap_zero_refuses_every_admit_and_available_balance_as_of(
    tmp_path: Path,
) -> None:
    """Break covered: hard_stop_usd=0 or available_balance snapshot failing to cap admits."""
    cfg_zero = _make_cfg(
        tmp_path / "zero",
        hard_stop_usd=Decimal("0"),
        mcp_connector_stop_usd=Decimal("0"),
    )
    policy_zero = CreditPolicy(cfg_zero)
    res_zero = policy_zero.admit(
        "req-zero",
        "anthropic/claude-sonnet-5",
        {
            "model": "claude-sonnet-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
        },
        token_count=1,
        now=_T0,
    )
    assert isinstance(res_zero, Refuse)
    assert res_zero.code == "anthropic_cycle_budget_exhausted"
    assert res_zero.http_status == 429
    assert res_zero.headroom_usd == Decimal("0")

    # available_balance.as_of with $2.00 remaining as of _T0
    cfg_bal = _make_cfg(
        tmp_path / "bal",
        available_balance=AvailableBalanceConfig(
            usd=Decimal("2.00"),
            as_of=_T0.isoformat(),
            owner="kaywan",
        ),
    )
    policy_bal = CreditPolicy(cfg_bal)
    r1 = policy_bal.admit(
        "bal-1",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    r2 = policy_bal.admit(
        "bal-2",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    r3 = policy_bal.admit(
        "bal-3",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=_T0,
    )
    assert isinstance(r1, Admit)
    assert isinstance(r2, Admit)
    assert isinstance(r3, Refuse)
    assert r3.code == "anthropic_cycle_budget_exhausted"
