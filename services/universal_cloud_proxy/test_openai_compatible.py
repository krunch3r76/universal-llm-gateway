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
    recorder: list[dict[str, object]] | None = None,
) -> OpenAICompatibleAdapter:
    async def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(
                {"url": str(request.url), "body": json.loads(request.content.decode())}
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
    )
    return OpenAICompatibleAdapter(config=config, client=client)


@pytest.mark.asyncio
async def test_xai_uses_chat_completions() -> None:
    """xAI requests route to /chat/completions with upstream model ID."""
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
