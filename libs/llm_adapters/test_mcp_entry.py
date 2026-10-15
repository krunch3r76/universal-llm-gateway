"""Unit tests for MCP public URL normalization (friction 24366)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llm_adapters._mcp_entry import (
    AnthropicCallbackTokenError,
    normalize_mcp_public_url,
    resolve_anthropic_callback_token,
    resolve_mcp_env,
)


@pytest.fixture(autouse=True)
def _pin_mcp_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "mcp.yaml"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    return cfg


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp/code/"),
        ("https://mcp.example.com/mcp/", "https://mcp.example.com/mcp/code/"),
        ("https://mcp.example.com/mcp/code", "https://mcp.example.com/mcp/code/"),
        ("https://mcp.example.com/mcp/code/", "https://mcp.example.com/mcp/code/"),
        ("https://mcp.example.com/mcp/life", "https://mcp.example.com/mcp/life/"),
        ("  https://mcp.example.com/mcp/code  ", "https://mcp.example.com/mcp/code/"),
    ],
)
def test_normalize_mcp_public_url(raw: str, expected: str) -> None:
    assert normalize_mcp_public_url(raw) == expected


def test_resolve_mcp_env_rewrites_defunct_bare_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    url, token = resolve_mcp_env()
    assert url == "https://mcp.example.com/mcp/code/"
    assert token == "tok"


def test_resolve_anthropic_callback_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_ANTHROPIC_CALLBACK_TOKEN", "callback-env")
    assert resolve_anthropic_callback_token() == "callback-env"


def test_resolve_anthropic_callback_yaml_auth_token_collision_raises(
    _pin_mcp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_mcp_config.write_text(
        yaml.safe_dump(
            {
                "auth_token": "yaml-admin",
                "anthropic_callback_token": "yaml-admin",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_AUTH_TOKEN", "env-admin")
    with pytest.raises(AnthropicCallbackTokenError):
        resolve_anthropic_callback_token()


def test_resolve_anthropic_callback_auth_token_env_collision_raises(
    _pin_mcp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_mcp_config.write_text(
        yaml.safe_dump(
            {
                "auth_token": "unused-inline",
                "auth_token_env": "MCP_ADMIN_ENV",
                "anthropic_callback_token": "shared-with-env-admin",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_ADMIN_ENV", "shared-with-env-admin")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(AnthropicCallbackTokenError):
        resolve_anthropic_callback_token()


def test_resolve_anthropic_callback_returns_distinct_token(
    _pin_mcp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_mcp_config.write_text(
        yaml.safe_dump(
            {
                "auth_token": "yaml-admin",
                "anthropic_callback_token": "callback-only",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_AUTH_TOKEN", "env-admin")
    assert resolve_anthropic_callback_token() == "callback-only"
