"""Tests for cortex-api digest-close T1 env pin."""

from __future__ import annotations

from scripts.model_manager.ui.controller.service_config import (
    apply_cortex_digest_close_env_pin,
)


def test_apply_cortex_digest_close_env_pin_defaults():
    env: dict[str, str] = {}
    apply_cortex_digest_close_env_pin(env)
    assert env["CORTEX_DIGEST_CLOSE_HOOK"] == "1"
    assert env["JOURNAL_BRIDGE_URL"] == "http://localhost:8200"
    assert "BRIDGE_TOKEN" not in env


def test_apply_cortex_digest_close_env_pin_mcp_config_wins():
    env = {
        "JOURNAL_BRIDGE_URL": "http://stale:8200",
        "BRIDGE_TOKEN": "stale-token",
    }
    apply_cortex_digest_close_env_pin(
        env,
        bridge_token="fleet-token",
        journal_bridge_url="http://journal-bridge:8200",
    )
    assert env["CORTEX_DIGEST_CLOSE_HOOK"] == "1"
    assert env["JOURNAL_BRIDGE_URL"] == "http://journal-bridge:8200"
    assert env["BRIDGE_TOKEN"] == "fleet-token"


def test_apply_cortex_digest_close_env_pin_preserves_existing_bridge_token():
    env = {"BRIDGE_TOKEN": "from-secrets"}
    apply_cortex_digest_close_env_pin(env, journal_bridge_url="http://custom:8200")
    assert env["BRIDGE_TOKEN"] == "from-secrets"
    assert env["JOURNAL_BRIDGE_URL"] == "http://custom:8200"
