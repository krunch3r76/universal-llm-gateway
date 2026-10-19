"""Tests for MCP_ANTHROPIC_CALLBACK_TOKEN backfill in build_service_env."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.model_manager.ui.controller import service_config


@pytest.fixture(autouse=True)
def _hermetic_gateway_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read operator passwd ~/.gateway/secrets.env under dispatch HOME."""
    gw = tmp_path / "gateway"
    gw.mkdir()
    monkeypatch.setattr(service_config, "GATEWAY_DIR", gw)


@pytest.fixture
def tmp_mcp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mcp.yaml"
    monkeypatch.setattr(service_config, "_MCP_CONFIG_PATH", path)
    monkeypatch.setenv("MCP_CONFIG_PATH", str(path))
    return path


def test_backfill_from_yaml_when_env_unset(
    tmp_mcp_yaml: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "admin-token",
                "anthropic_callback_token": "callback-only-token",
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_service_env(tmp_path)
    assert env.get("MCP_ANTHROPIC_CALLBACK_TOKEN") == "callback-only-token"


def test_existing_env_not_overwritten(
    tmp_mcp_yaml: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_ANTHROPIC_CALLBACK_TOKEN", "env-callback")
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "admin-token",
                "anthropic_callback_token": "yaml-callback",
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_service_env(tmp_path)
    assert env.get("MCP_ANTHROPIC_CALLBACK_TOKEN") == "env-callback"


def test_collision_with_mcp_auth_token_skips_backfill(
    tmp_mcp_yaml: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "same-token")
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "admin-token",
                "anthropic_callback_token": "same-token",
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_service_env(tmp_path)
    assert "MCP_ANTHROPIC_CALLBACK_TOKEN" not in env


def test_collision_with_yaml_auth_token_skips_backfill(
    tmp_mcp_yaml: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "shared-admin",
                "anthropic_callback_token": "shared-admin",
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_service_env(tmp_path)
    assert "MCP_ANTHROPIC_CALLBACK_TOKEN" not in env


def test_missing_yaml_key_does_not_raise(
    tmp_mcp_yaml: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    tmp_mcp_yaml.write_text(
        yaml.safe_dump({"auth_token": "admin-token"}),
        encoding="utf-8",
    )
    env = service_config.build_service_env(tmp_path)
    assert "MCP_ANTHROPIC_CALLBACK_TOKEN" not in env
