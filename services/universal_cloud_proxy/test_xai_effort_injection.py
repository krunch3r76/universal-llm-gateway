"""End-to-end unit tests for xAI __effort_<value> model-suffix injection.

Verifies that _forward_native correctly:
- Strips __effort_<tier> suffix from the model ID before forwarding.
- Injects the decoded value into reasoning.effort in the upstream body.
- Respects caller-wins: body-supplied reasoning.effort takes precedence.
- Passes unknown (bogus) effort values through without validation.

Uses a recording mock forwarder to capture the exact body that would be
sent upstream — the same pattern as test_openai_reasoning_strip.py.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from services.universal_cloud_proxy.native_routes import _forward_native


def _make_xai_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/providers/xai/responses",
        "raw_path": b"/api/v1/providers/xai/responses",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope)


class _RecordingForwarder:
    """Non-streaming forwarder that records the upstream request body."""

    def __init__(self) -> None:
        self.recorded: list[dict] = []

    def adapter_type(self, provider: str) -> str:
        _ = provider
        return "openai_compatible"

    async def forward_native(self, *, provider: str, request_body: dict) -> dict:
        _ = provider
        self.recorded.append(dict(request_body))
        return {
            "id": "resp_test",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}
            ],
        }


async def _run(body: dict) -> tuple[dict, list[dict]]:
    """Call _forward_native with the given body; return (response_body, upstream_calls)."""
    request = _make_xai_request()
    forwarder = _RecordingForwarder()
    with patch(
        "services.universal_cloud_proxy.cloud_proxy._read_json_object_body",
        new_callable=AsyncMock,
        return_value=body,
    ):
        resp = await _forward_native(
            request,
            provider_key="xai",
            surface="test",
            forwarder=forwarder,
            event_bus=None,
        )
    resp_body: dict = {}
    if isinstance(resp, JSONResponse):
        resp_body = json.loads(resp.body)
    return resp_body, forwarder.recorded


# ---------------------------------------------------------------------------
# Four effort tiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "suffix,expected_effort",
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "xhigh"),
    ],
)
@pytest.mark.asyncio
async def test_effort_tier_injected(suffix: str, expected_effort: str) -> None:
    body = {"model": f"grok-4.6__effort_{suffix}", "input": "ping"}
    _, calls = await _run(body)
    assert calls, "forwarder not called"
    upstream = calls[0]
    assert upstream["model"] == "grok-4.6", (
        f"suffix not stripped — got {upstream['model']!r}"
    )
    assert upstream.get("reasoning", {}).get("effort") == expected_effort, (
        f"wrong effort injected: {upstream.get('reasoning')}"
    )


# ---------------------------------------------------------------------------
# Caller-wins: body-supplied reasoning.effort takes precedence over suffix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_wins_precedence() -> None:
    """Suffix=low but caller sets reasoning.effort=high — high must win."""
    body = {
        "model": "grok-4.6__effort_low",
        "input": "ping",
        "reasoning": {"effort": "high"},
    }
    _, calls = await _run(body)
    assert calls
    upstream = calls[0]
    assert upstream["model"] == "grok-4.6"
    assert upstream["reasoning"]["effort"] == "high", (
        f"caller-wins failed — got effort={upstream['reasoning']['effort']!r}"
    )


# ---------------------------------------------------------------------------
# Bogus suffix — cloud-proxy does not validate effort values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bogus_suffix_passes_through() -> None:
    """Unrecognised effort values are injected without validation (xAI rejects them).

    Cloud-proxy is not the enforcement boundary for xAI enum validation.
    The suffix is still stripped from the model ID.
    """
    body = {"model": "grok-4.6__effort_bogus", "input": "ping"}
    _, calls = await _run(body)
    assert calls
    upstream = calls[0]
    assert upstream["model"] == "grok-4.6", "model not stripped for bogus suffix"
    assert upstream.get("reasoning", {}).get("effort") == "bogus", (
        f"bogus effort not injected: {upstream.get('reasoning')}"
    )
