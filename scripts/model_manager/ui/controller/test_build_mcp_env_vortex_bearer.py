"""Tests for VORTEX_BEARER_TOKENS passthrough in build_mcp_env."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.model_manager.ui.controller import service_config


@pytest.fixture
def gateway_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gw = tmp_path / "gateway"
    gw.mkdir()
    monkeypatch.setattr(service_config, "GATEWAY_DIR", gw)
    return gw


@pytest.fixture
def tmp_mcp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mcp.yaml"
    monkeypatch.setattr(service_config, "_MCP_CONFIG_PATH", path)
    monkeypatch.setenv("MCP_CONFIG_PATH", str(path))
    return path


def test_build_mcp_env_includes_vortex_key_when_secrets_has_it(
    gateway_dir: Path,
    tmp_mcp_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (gateway_dir / "secrets.env").write_text(
        "VORTEX_BEARER_TOKENS=open-webui fixture-label-only\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VORTEX_BEARER_TOKENS", raising=False)
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "admin-token",
                "project_dir": "/tmp/mcp-project",
                "data_dir": str(tmp_path / "mcp-data"),
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_mcp_env(tmp_path)
    assert "VORTEX_BEARER_TOKENS" in env


def test_build_mcp_env_succeeds_when_vortex_absent(
    gateway_dir: Path,
    tmp_mcp_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (gateway_dir / "secrets.env").write_text("", encoding="utf-8")
    monkeypatch.delenv("VORTEX_BEARER_TOKENS", raising=False)
    tmp_mcp_yaml.write_text(
        yaml.safe_dump(
            {
                "auth_token": "admin-token",
                "project_dir": "/tmp/mcp-project",
                "data_dir": str(tmp_path / "mcp-data"),
            }
        ),
        encoding="utf-8",
    )
    env = service_config.build_mcp_env(tmp_path)
    assert isinstance(env, dict)
