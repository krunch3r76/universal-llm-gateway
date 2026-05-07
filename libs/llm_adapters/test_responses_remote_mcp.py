"""Unit tests for Responses API adapter remote_mcp handling.

Covers the remote_mcp=True branch for OpenAI vendor:

- body["tools"] receives the shared-helper MCP descriptor entry
- Strict contract raises when mixed with non-MCP tools or mcp_tool_loop=True
- resolve_mcp_env() raises when env vars are missing
- remote_mcp=False leaves tools handling untouched
- xAI raises RemoteMcpUnsupportedError (xAI does not yet support type:mcp)
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.responses import RemoteMcpUnsupportedError, ResponsesAPIAdapter


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


@pytest.mark.parametrize("model", ["gpt-5.4"])
def test_remote_mcp_appends_tool_entry(model: str) -> None:
    req = _base_req(model, remote_mcp=True)
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert body["tools"] == [_EXPECTED_ENTRY]


def test_remote_mcp_xai_raises_unsupported() -> None:
    req = _base_req("grok-4.20-multi-agent-0309", remote_mcp=True)
    with pytest.raises(RemoteMcpUnsupportedError, match="xAI does not yet support"):
        _adapter("xai").build_frontier_request(req)


def test_remote_mcp_coexists_with_prior_mcp_entries() -> None:
    prior = {"type": "mcp", "server_url": "other", "server_label": "other"}
    req = _base_req("gpt-5.4", remote_mcp=True, tools=[prior])
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    assert body["tools"] == [prior, _EXPECTED_ENTRY]


def test_remote_mcp_rejects_non_mcp_tools() -> None:
    req = _base_req(
        "gpt-5.4",
        remote_mcp=True,
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    with pytest.raises(ValueError, match="only accepts provider-native"):
        _adapter("openai").build_frontier_request(req)


def test_remote_mcp_rejects_mcp_tool_loop_true() -> None:
    req = _base_req("gpt-5.4", remote_mcp=True, mcp_tool_loop=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _adapter("openai").build_frontier_request(req)


def test_remote_mcp_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    req = _base_req("gpt-5.4", remote_mcp=True)
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        _adapter("openai").build_frontier_request(req)


def test_remote_mcp_off_preserves_normal_tool_flow() -> None:
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    req = _base_req("grok-4-fast-reasoning", tools=tools)
    _url, _headers, body = _adapter("xai").build_frontier_request(req)
    assert body["tools"] == [
        {
            "type": "function",
            "name": "f",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_client_side_function_tools_are_sanitized() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch a URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "title": "URL"},
                        "headers": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "properties": {"Authorization": {"type": "string"}},
                                    "additionalProperties": False,
                                },
                                {"type": "null"},
                            ],
                            "default": None,
                        },
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    req = _base_req("gpt-5.4", tools=tools)
    _url, _headers, body = _adapter("openai").build_frontier_request(req)
    params = body["tools"][0]["parameters"]

    assert body["tools"][0]["name"] == "web_fetch"
    assert "additionalProperties" not in params
    assert "title" not in params["properties"]["url"]
    assert params["properties"]["headers"]["type"] == "object"
    assert "default" not in params["properties"]["headers"]
