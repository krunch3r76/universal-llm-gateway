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
