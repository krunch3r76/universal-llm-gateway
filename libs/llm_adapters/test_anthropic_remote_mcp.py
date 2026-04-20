"""Unit tests for Anthropic adapter remote_mcp handling.

Covers FrontierRequest.remote_mcp=True branch in
``AnthropicAdapter.build_frontier_request``:

- body["mcp_servers"] is populated with the shared-helper shape
- anthropic-beta header includes the mcp-client beta automatically
- Strict contract raises when mixed with req.tools or mcp_tool_loop=True
- resolve_mcp_env() raises when env vars are missing
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.anthropic import AnthropicAdapter


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token-xyz")


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="k-test", base_url="https://api.anthropic.com")


def _base_req(**overrides) -> FrontierRequest:
    base = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
    }
    base.update(overrides)
    return FrontierRequest(**base)


def test_remote_mcp_populates_mcp_servers() -> None:
    req = _base_req(remote_mcp=True)
    _url, headers, body = _adapter().build_frontier_request(req)

    assert body["mcp_servers"] == [
        {
            "type": "url",
            "name": "vortex",
            "url": "https://mcp.example.com/mcp",
            "authorization_token": "test-token-xyz",
        }
    ]
    assert "mcp-client-2025-11-20" in headers["anthropic-beta"]
    assert body["tools"] == [{"type": "mcp_toolset", "mcp_server_name": "vortex"}]


def test_remote_mcp_rejects_mcp_tool_loop_true() -> None:
    req = _base_req(remote_mcp=True, mcp_tool_loop=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _adapter().build_frontier_request(req)


def test_remote_mcp_rejects_non_empty_tools() -> None:
    req = _base_req(
        remote_mcp=True,
        tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
    )
    with pytest.raises(ValueError, match="rejects any req.tools"):
        _adapter().build_frontier_request(req)


def test_remote_mcp_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    req = _base_req(remote_mcp=True)
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        _adapter().build_frontier_request(req)


def test_remote_mcp_off_leaves_tools_path_untouched() -> None:
    req = _base_req(
        tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
    )
    _url, headers, body = _adapter().build_frontier_request(req)
    assert "mcp_servers" not in body
    assert body.get("tools") == [{"name": "f", "description": "", "input_schema": {}}]
    assert "mcp-client-2025-11-20" not in headers.get("anthropic-beta", "")
