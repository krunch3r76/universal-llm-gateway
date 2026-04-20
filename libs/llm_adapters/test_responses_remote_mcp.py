"""Unit tests for Responses API adapter remote_mcp handling.

Covers the remote_mcp=True branch for both xAI and OpenAI vendor variants:

- body["tools"] receives the shared-helper MCP descriptor entry
- Strict contract raises when mixed with non-MCP tools or mcp_tool_loop=True
- resolve_mcp_env() raises when env vars are missing
- remote_mcp=False leaves tools handling untouched
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.responses import ResponsesAPIAdapter


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token-xyz")


def _adapter(vendor: str) -> ResponsesAPIAdapter:
    base = "https://api.x.ai/v1" if vendor == "xai" else "https://api.openai.com/v1"
    return ResponsesAPIAdapter(api_key="k-test", base_url=base, vendor=vendor)


def _base_req(model: str, **overrides) -> FrontierRequest:
    base = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": model,
        "max_tokens": 4096,
    }
    base.update(overrides)
    return FrontierRequest(**base)


_EXPECTED_ENTRY = {
    "type": "mcp",
    "server_url": "https://mcp.example.com/mcp",
    "server_label": "vortex",
    "authorization": "Bearer test-token-xyz",
    "require_approval": "never",
}


@pytest.mark.parametrize(
    "vendor,model", [("xai", "grok-4.20-multi-agent-0309"), ("openai", "gpt-5.4")]
)
def test_remote_mcp_appends_tool_entry(vendor: str, model: str) -> None:
    req = _base_req(model, remote_mcp=True)
    _url, _headers, body = _adapter(vendor).build_frontier_request(req)
    assert body["tools"] == [_EXPECTED_ENTRY]


def test_remote_mcp_coexists_with_prior_mcp_entries() -> None:
    prior = {"type": "mcp", "server_url": "other", "server_label": "other"}
    req = _base_req("grok-4.20-multi-agent-0309", remote_mcp=True, tools=[prior])
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body["tools"] == [prior, _EXPECTED_ENTRY]


def test_remote_mcp_rejects_non_mcp_tools() -> None:
    req = _base_req(
        "grok-4-fast-reasoning",
        remote_mcp=True,
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    with pytest.raises(ValueError, match="only accepts provider-native"):
        _adapter("xai").build_frontier_request(req)


def test_remote_mcp_rejects_mcp_tool_loop_true() -> None:
    req = _base_req("gpt-5.4", remote_mcp=True, mcp_tool_loop=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _adapter("openai").build_frontier_request(req)


def test_remote_mcp_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    req = _base_req("grok-4.20-multi-agent-0309", remote_mcp=True)
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        _adapter("xai").build_frontier_request(req)


def test_remote_mcp_off_preserves_normal_tool_flow() -> None:
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    req = _base_req("grok-4-fast-reasoning", tools=tools)
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body["tools"] == [{"type": "function", "name": "f", "parameters": {}}]
