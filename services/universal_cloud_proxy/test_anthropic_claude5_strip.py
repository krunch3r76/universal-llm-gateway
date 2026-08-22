"""Regression tests — Claude 5 / Opus 4.7+ sampling-knob stripping.

Covers the adapter-layer fix that removes ``temperature`` / ``top_p`` /
``top_k`` from Anthropic Messages payloads for families that 400 on
non-default sampling (``claude-sonnet-5``, ``claude-opus-5``,
``claude-opus-4-7``). Claude 4.6 and earlier must pass through unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from services.universal_cloud_proxy.adapters.anthropic import AnthropicAdapter
from services.universal_cloud_proxy.adapters.anthropic_sampling import (
    _is_claude5_sampling_blocked_model,
    _strip_claude5_incompatible_params,
)
from services.universal_cloud_proxy.config import ProviderConfig


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-5",
        "claude-sonnet-5-mcp",
        "anthropic/claude-sonnet-5",
        "claude-opus-5",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-haiku-5",
        "claude-fable-5",
        "CLAUDE-SONNET-5",
    ],
)
def test_is_claude5_sampling_blocked_model_positive(model: str) -> None:
    assert _is_claude5_sampling_blocked_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-6",
        "claude-opus-4",
        "claude-opus-4-6",
        "claude-3-5-sonnet",
        "gpt-5",
        "",
    ],
)
def test_is_claude5_sampling_blocked_model_negative(model: str) -> None:
    assert _is_claude5_sampling_blocked_model(model) is False


def test_strip_removes_all_three_params_for_sonnet5() -> None:
    body: dict[str, object] = {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 10,
        "max_tokens": 256,
    }
    stripped = _strip_claude5_incompatible_params(
        body, upstream_model="claude-sonnet-5"
    )
    assert sorted(stripped) == ["temperature", "top_k", "top_p"]
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body
    assert body["max_tokens"] == 256
    assert body["model"] == "claude-sonnet-5"


def test_strip_noop_for_sonnet46() -> None:
    body: dict[str, object] = {
        "model": "claude-sonnet-4-6",
        "temperature": 0.2,
        "top_p": 0.9,
    }
    stripped = _strip_claude5_incompatible_params(
        body, upstream_model="claude-sonnet-4-6"
    )
    assert stripped == []
    assert body == {
        "model": "claude-sonnet-4-6",
        "temperature": 0.2,
        "top_p": 0.9,
    }


def test_strip_is_idempotent_when_params_absent() -> None:
    body: dict[str, object] = {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
    }
    stripped = _strip_claude5_incompatible_params(
        body, upstream_model="claude-sonnet-5"
    )
    assert stripped == []
    assert body == {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
    }


def test_strip_partial_subset() -> None:
    body: dict[str, object] = {
        "model": "claude-opus-4-7",
        "temperature": 0.0,
    }
    stripped = _strip_claude5_incompatible_params(
        body, upstream_model="claude-opus-4-7"
    )
    assert stripped == ["temperature"]
    assert "temperature" not in body


def _make_adapter(
    *,
    recorder: list[dict[str, object]] | None = None,
) -> AnthropicAdapter:
    async def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(
                {"url": str(request.url), "body": json.loads(request.content.decode())}
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider="anthropic",
        api_key="provider-key",
        base_url="https://api.example.com/v1",
    )
    return AnthropicAdapter(config=config, client=client)


def test_openai_to_anthropic_strips_sampling_for_sonnet5() -> None:
    adapter = _make_adapter()
    result = adapter._openai_to_anthropic(
        {
            "model": "anthropic/claude-sonnet-5",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 10,
            "max_tokens": 256,
        }
    )
    assert "temperature" not in result
    assert "top_p" not in result
    assert "top_k" not in result
    assert result["max_tokens"] == 256
    assert result["model"] == "claude-sonnet-5"


def test_openai_to_anthropic_keeps_temperature_for_sonnet46() -> None:
    adapter = _make_adapter()
    result = adapter._openai_to_anthropic(
        {
            "model": "anthropic/claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "max_tokens": 256,
        }
    )
    assert result["temperature"] == 0.2
    assert result["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_forward_native_strips_temperature_for_sonnet5() -> None:
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "max_tokens": 64,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 10,
        }
    )
    assert recorded
    body = recorded[0]["body"]
    assert isinstance(body, dict)
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 64


@pytest.mark.asyncio
async def test_forward_native_preserves_temperature_for_sonnet46() -> None:
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(recorder=recorded)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "max_tokens": 64,
            "temperature": 0.2,
        }
    )
    assert recorded
    body = recorded[0]["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.2
    assert body["model"] == "claude-sonnet-4-6"
