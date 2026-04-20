"""B1 regression tests — OpenAI reasoning-model param stripping.

Covers the adapter-layer fix in ``openai_compatible.py`` that removes
``temperature`` / ``top_p`` / ``presence_penalty`` / ``frequency_penalty``
from requests targeting OpenAI reasoning families (``gpt-5``, ``o1``,
``o3``, ``o4``). Non-reasoning OpenAI models and xAI Grok must pass through
unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from services.universal_cloud_proxy.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
    _is_openai_reasoning_model,
    _strip_reasoning_incompatible_params,
)
from services.universal_cloud_proxy.config import ProviderConfig

# ---------------------------------------------------------------------------
# Pure-helper tests: classifier + strip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5.0",
        "gpt-5.1-turbo",
        "o1",
        "o1-preview",
        "o3-mini",
        "o4-mini",
        "GPT-5",
    ],
)
def test_is_openai_reasoning_model_positive(model: str) -> None:
    assert _is_openai_reasoning_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
        "grok-4-fast-reasoning",
        "claude-sonnet-4",
        "",
    ],
)
def test_is_openai_reasoning_model_negative(model: str) -> None:
    assert _is_openai_reasoning_model(model) is False


def test_strip_noop_for_non_openai_provider() -> None:
    """xAI Grok accepts temperature/top_p — strip must be provider-gated."""
    body: dict[str, object] = {
        "model": "gpt-5",
        "temperature": 0.7,
        "top_p": 0.9,
    }
    stripped = _strip_reasoning_incompatible_params(
        body, provider="xai", upstream_model="gpt-5"
    )
    assert stripped == []
    assert body == {"model": "gpt-5", "temperature": 0.7, "top_p": 0.9}


def test_strip_noop_for_openai_non_reasoning() -> None:
    """gpt-4o and friends still accept temperature — strip must be model-gated."""
    body: dict[str, object] = {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "top_p": 0.9,
    }
    stripped = _strip_reasoning_incompatible_params(
        body, provider="openai", upstream_model="gpt-4o-mini"
    )
    assert stripped == []
    assert body == {"model": "gpt-4o-mini", "temperature": 0.7, "top_p": 0.9}


def test_strip_removes_all_four_params_for_reasoning() -> None:
    body: dict[str, object] = {
        "model": "gpt-5",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7,
        "top_p": 0.9,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "max_completion_tokens": 100,
    }
    stripped = _strip_reasoning_incompatible_params(
        body, provider="openai", upstream_model="gpt-5"
    )
    assert sorted(stripped) == [
        "frequency_penalty",
        "presence_penalty",
        "temperature",
        "top_p",
    ]
    assert "temperature" not in body
    assert "top_p" not in body
    assert "presence_penalty" not in body
    assert "frequency_penalty" not in body
    assert body["max_completion_tokens"] == 100
    assert body["model"] == "gpt-5"


def test_strip_is_idempotent_when_params_absent() -> None:
    body: dict[str, object] = {
        "model": "gpt-5",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 100,
    }
    stripped = _strip_reasoning_incompatible_params(
        body, provider="openai", upstream_model="gpt-5"
    )
    assert stripped == []
    assert body == {
        "model": "gpt-5",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 100,
    }


def test_strip_partial_subset() -> None:
    """Only keys actually present in the body are reported as stripped."""
    body: dict[str, object] = {
        "model": "o3-mini",
        "temperature": 0.0,
    }
    stripped = _strip_reasoning_incompatible_params(
        body, provider="openai", upstream_model="o3-mini"
    )
    assert stripped == ["temperature"]
    assert "temperature" not in body


# ---------------------------------------------------------------------------
# Adapter integration: _prepare_chat_body + forward_native
# ---------------------------------------------------------------------------


def _make_adapter(
    *,
    provider: str,
    recorder: list[dict[str, object]] | None = None,
) -> OpenAICompatibleAdapter:
    async def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(
                {"url": str(request.url), "body": json.loads(request.content.decode())}
            )
        return httpx.Response(
            200,
            json={"id": "resp_123", "object": "chat.completion", "choices": []},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider=provider,
        api_key="provider-key",
        base_url="https://api.example.com/v1",
    )
    return OpenAICompatibleAdapter(config=config, client=client)


def test_prepare_chat_body_strips_temperature_for_gpt5() -> None:
    """End-to-end: _prepare_chat_body drops temperature AND converts max_tokens for gpt-5."""
    adapter = _make_adapter(provider="openai")
    result = adapter._prepare_chat_body(
        {
            "model": "openai/gpt-5",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 256,
        }
    )
    assert "temperature" not in result
    assert "top_p" not in result
    assert "max_tokens" not in result
    assert result["max_completion_tokens"] == 256
    assert result["model"] == "gpt-5"


def test_prepare_chat_body_keeps_temperature_for_gpt4o() -> None:
    adapter = _make_adapter(provider="openai")
    result = adapter._prepare_chat_body(
        {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
            "max_tokens": 256,
        }
    )
    assert result["temperature"] == 0.7
    assert "max_tokens" not in result
    assert result["max_completion_tokens"] == 256


@pytest.mark.asyncio
async def test_forward_native_strips_temperature_for_gpt5() -> None:
    """Responses API path must drop temperature for OpenAI reasoning models."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(provider="openai", recorder=recorded)
    await adapter.forward_native(
        {
            "model": "gpt-5",
            "input": "hi",
            "temperature": 0.7,
            "top_p": 0.9,
        }
    )
    assert recorded
    body = recorded[0]["body"]
    assert isinstance(body, dict)
    assert "temperature" not in body
    assert "top_p" not in body
    assert body["model"] == "gpt-5"


@pytest.mark.asyncio
async def test_forward_native_preserves_temperature_for_xai() -> None:
    """xAI Grok traverses forward_native too — temperature MUST pass through."""
    recorded: list[dict[str, object]] = []
    adapter = _make_adapter(provider="xai", recorder=recorded)
    await adapter.forward_native(
        {
            "model": "grok-4-fast-reasoning",
            "input": "hi",
            "temperature": 0.7,
        }
    )
    assert recorded
    body = recorded[0]["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.7
    assert body["model"] == "grok-4-fast-reasoning"
