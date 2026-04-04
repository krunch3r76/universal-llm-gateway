from __future__ import annotations

import json

import httpx
import pytest

from services.universal_cloud_proxy.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
)
from services.universal_cloud_proxy.config import ProviderConfig


def _make_adapter(
    *,
    provider: str = "xai",
    mcp_server_url: str | None = "https://mcp.example.com/mcp",
    mcp_auth_token: str | None = "secret-token",
    recorder: list[dict[str, object]] | None = None,
) -> OpenAICompatibleAdapter:
    async def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(
                {"url": str(request.url), "body": json.loads(request.content.decode())}
            )
        if "/responses" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "resp_456",
                    "output_text": "Hello from MCP tools",
                    "output": [],
                    "usage": {"input_tokens": 12, "output_tokens": 7},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_123",
                "object": "chat.completion",
                "choices": [],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider=provider,
        api_key="provider-key",
        base_url="https://api.example.com/v1",
        mcp_server_url=mcp_server_url,
        mcp_auth_token=mcp_auth_token,
    )
    return OpenAICompatibleAdapter(config=config, client=client)


@pytest.mark.asyncio
async def test_xai_mcp_routes_to_responses_api() -> None:
    """xAI MCP requests hit /responses with input + tools[type=mcp], no require_approval."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    result = await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning-mcp",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    assert recorded
    assert "/responses" in str(recorded[0]["url"])
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    assert upstream["model"] == "grok-4-fast-reasoning"
    assert "messages" not in upstream
    assert upstream["input"] == [{"role": "user", "content": "hi"}]
    assert upstream["store"] is False

    tools = upstream["tools"]
    assert len(tools) == 1
    mcp_tool = tools[0]
    assert mcp_tool == {
        "type": "mcp",
        "server_url": "https://mcp.example.com/mcp",
        "server_label": "vortex",
        "authorization": "Bearer secret-token",
    }
    assert "require_approval" not in mcp_tool

    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == "Hello from MCP tools"
    assert result["usage"]["prompt_tokens"] == 12
    assert result["usage"]["completion_tokens"] == 7


@pytest.mark.asyncio
async def test_xai_non_mcp_uses_chat_completions() -> None:
    """Normal xAI requests (no -mcp suffix) still use /chat/completions."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    assert recorded
    assert "/chat/completions" in str(recorded[0]["url"])
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    assert upstream["model"] == "grok-4-fast-reasoning"
    assert "input" not in upstream


@pytest.mark.asyncio
async def test_mcp_suffix_respects_tool_choice_none() -> None:
    """tool_choice=none suppresses MCP injection even with -mcp suffix."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning-mcp",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": "none",
        }
    )

    assert recorded
    assert "/chat/completions" in str(recorded[0]["url"])
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    assert upstream["model"] == "grok-4-fast-reasoning"
    assert "tools" not in upstream


@pytest.mark.asyncio
async def test_xai_mcp_converts_multimodal_content_parts() -> None:
    """image_url parts are converted to input_image for the Responses API."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning-mcp",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,iVBORw0KGgo=",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
        }
    )

    assert recorded
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    input_msgs = upstream["input"]
    assert len(input_msgs) == 1
    content = input_msgs[0]["content"]
    assert content == [
        {"type": "input_text", "text": "What is in this image?"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,iVBORw0KGgo=",
            "detail": "high",
        },
    ]


@pytest.mark.asyncio
async def test_xai_mcp_preserves_string_content() -> None:
    """Plain string content is passed through unchanged."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning-mcp",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert recorded
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    assert upstream["input"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_xai_mcp_max_tokens_maps_to_max_output_tokens() -> None:
    """max_tokens in the request maps to max_output_tokens for the Responses API."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)

    await adapter.forward_chat(
        {
            "model": "xai/grok-4-fast-reasoning-mcp",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 512,
        }
    )

    assert recorded
    upstream = recorded[0]["body"]
    assert isinstance(upstream, dict)
    assert upstream.get("max_output_tokens") == 512
    assert "max_tokens" not in upstream
