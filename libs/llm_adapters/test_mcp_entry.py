"""Unit tests for MCP public URL normalization (friction 24366)."""

from __future__ import annotations

import pytest

from llm_adapters._mcp_entry import normalize_mcp_public_url, resolve_mcp_env


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
