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
from llm_adapters._mcp_entry import ANTHROPIC_AGENTIC_MCP_TOOLS
from llm_adapters.anthropic import AnthropicAdapter


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin-token\nanthropic_callback_token: callback-token\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "admin-token")
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)


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
            "url": "https://mcp.example.com/mcp/code/",
            "authorization_token": "callback-token",
        }
    ]
    assert "mcp-client-2025-11-20" in headers["anthropic-beta"]
    toolset = body["tools"][0]
    assert toolset["type"] == "mcp_toolset"
    assert toolset["mcp_server_name"] == "vortex"
    assert toolset["default_config"] == {"enabled": False}
    assert set(toolset["configs"]) == set(ANTHROPIC_AGENTIC_MCP_TOOLS)


def test_remote_mcp_opus_toolset_configs_and_cache_control() -> None:
    """Frontier opus remote_mcp gets agentic configs and ephemeral cache_control.

    Breaks when build_frontier_request emits a bare mcp_toolset (full catalog,
    no breakpoint) for remote-connector cards.
    """
    req = _base_req(model="claude-opus-4-8", remote_mcp=True)
    _url, _headers, body = _adapter().build_frontier_request(req)
    toolset = body["tools"][0]
    assert toolset["configs"] == {
        name: {"enabled": True} for name in sorted(ANTHROPIC_AGENTIC_MCP_TOOLS)
    }
    assert "team_dispatch" in toolset["configs"]
    assert "agent_bus" in toolset["configs"]
    assert toolset["cache_control"] == {"type": "ephemeral"}


def test_remote_mcp_rollback_restores_bare_toolset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy off restores the bare toolset on the frontier path.

    Same flag the inject path reads. Breaks if the adapter builds the
    toolset inline and ignores the shared rollback.
    """
    monkeypatch.setattr(
        "llm_adapters._mcp_entry._APPLY_ANTHROPIC_TOOLSET_POLICY", False
    )
    req = _base_req(model="claude-opus-4-8", remote_mcp=True)
    _url, _headers, body = _adapter().build_frontier_request(req)
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
