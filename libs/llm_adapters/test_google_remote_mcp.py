"""Unit tests for Google adapter remote_mcp handling.

Google Gemini has no native remote-MCP protocol; the adapter must raise
``NotImplementedError`` when ``FrontierRequest.remote_mcp=True``.
"""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.google import GoogleAdapter


def _adapter() -> GoogleAdapter:
    return GoogleAdapter(
        api_key="k-test",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )


def _base_req(**overrides) -> FrontierRequest:
    base = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "gemini-2.5-pro",
        "max_tokens": 4096,
    }
    base.update(overrides)
    return FrontierRequest(**base)


def test_remote_mcp_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="google has no native remote MCP"):
        _adapter().build_frontier_request(_base_req(remote_mcp=True))


def test_remote_mcp_off_does_not_raise() -> None:
    _url, _headers, body = _adapter().build_frontier_request(_base_req())
    assert "contents" in body


def test_function_tools_are_sanitized_for_google() -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "title": "URL"},
                    "headers": {
                        "type": "object",
                        "properties": {"Authorization": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    }

    _url, _headers, body = _adapter().build_frontier_request(_base_req(tools=[tool]))
    function_decls = body["tools"][0]["functionDeclarations"]
    params = function_decls[0]["parameters"]

    assert function_decls[0]["name"] == "web_fetch"
    assert "additionalProperties" not in params
    assert "title" not in params["properties"]["url"]
    assert "additionalProperties" not in params["properties"]["headers"]
