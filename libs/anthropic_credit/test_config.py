"""Tests for anthropic credit config loader (fail closed)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from anthropic_credit.config import CreditConfigInvalid, load_credit_config


def test_shipped_yaml_loads() -> None:
    cfg = load_credit_config()
    assert cfg.hard_stop_usd == 185
    assert cfg.grant_usd == 200
    assert cfg.fail_mode == "closed"
    assert "anthropic/claude-sonnet-5" in cfg.metered_models
    assert cfg.cycle.anchor_day == 4
    assert cfg.cycle.owner == "kaywan"
    assert str(cfg.ledger_path).endswith("anthropic_credit.sqlite")


def _write_minimal_config(tmp_path: Path, **overrides: object) -> Path:
    base = {
        "cycle": {
            "anchor_day": 4,
            "anchor_time_utc": "00:00",
            "reset_tz": "unverified",
            "early_slack_hours": 14,
            "late_slack_hours": 48,
            "owner": "kaywan",
            "steward": "grok-bot-cc1",
        },
        "grant_usd": 200,
        "hard_stop_usd": 185,
        "mcp_connector_stop_usd": 170,
        "mcp_connector_allowance_usd": 5.0,
        "reservation_ttl_minutes": 15,
        "count_tokens_margin": 1.10,
        "fallback_overhead_tokens": 2000,
        "fallback_tokens_per_image": 5000,
        "ledger_path": "~/.gateway/state/anthropic_credit.sqlite",
        "reconcile_drift_usd": 1.00,
        "available_balance": {"usd": 200, "as_of": None, "owner": "kaywan"},
        "probe_cap_usd": 0.05,
        "metered_models": ["anthropic/claude-sonnet-5"],
        "standard_models": [],
        "high_cost_models": [],
        "fail_mode": "closed",
    }
    for key, value in overrides.items():
        if key == "cycle" and isinstance(value, dict):
            base["cycle"] = {**base["cycle"], **value}
        else:
            base[key] = value
    path = tmp_path / "anthropic_credit.yaml"
    path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("anchor_day", [0, 29])
def test_invalid_anchor_day(tmp_path: Path, anchor_day: int) -> None:
    path = _write_minimal_config(tmp_path, cycle={"anchor_day": anchor_day})
    with pytest.raises(CreditConfigInvalid, match="anchor_day"):
        load_credit_config(path)


def test_hard_stop_not_below_grant(tmp_path: Path) -> None:
    path = _write_minimal_config(tmp_path, hard_stop_usd=200, grant_usd=200)
    with pytest.raises(CreditConfigInvalid, match="hard_stop_usd"):
        load_credit_config(path)


def test_mcp_stop_above_hard_stop(tmp_path: Path) -> None:
    path = _write_minimal_config(tmp_path, mcp_connector_stop_usd=186)
    with pytest.raises(CreditConfigInvalid, match="mcp_connector_stop_usd"):
        load_credit_config(path)


def test_empty_cycle_owner(tmp_path: Path) -> None:
    path = _write_minimal_config(tmp_path, cycle={"owner": "  "})
    with pytest.raises(CreditConfigInvalid, match="cycle.owner"):
        load_credit_config(path)


def test_ledger_under_mnt(tmp_path: Path) -> None:
    path = _write_minimal_config(
        tmp_path, ledger_path="/mnt/torus/state/anthropic_credit.sqlite"
    )
    with pytest.raises(CreditConfigInvalid, match="/mnt/"):
        load_credit_config(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CreditConfigInvalid, match="not found"):
        load_credit_config(tmp_path / "missing.yaml")


def test_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("cycle: [unterminated\n", encoding="utf-8")
    with pytest.raises(CreditConfigInvalid, match="malformed YAML"):
        load_credit_config(path)
