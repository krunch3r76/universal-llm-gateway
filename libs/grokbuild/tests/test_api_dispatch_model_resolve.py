"""Tier-default model resolution for api_dispatch_op (mcp=False path)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from grokbuild.api_dispatch import api_dispatch_op
from grokbuild.constants import _XAI_GROK43_EFFORT_STANZA, default_model_for_tier


@pytest.mark.asyncio
async def test_api_dispatch_balanced_tier_resolves_effort_medium() -> None:
    captured: dict[str, Any] = {}

    async def _post(_path: str, *, json: dict[str, Any]) -> MagicMock:
        captured["model"] = json["model"]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "grokbuild.api_dispatch.make_async_client",
        return_value=client,
    ):
        envelope = await api_dispatch_op(
            cwd="/tmp",
            prompt="x",
            system_context=None,
            model=None,
            session_id=None,
            tier="balanced",
        )

    assert envelope["status"] == "completed"
    assert captured["model"] == _XAI_GROK43_EFFORT_STANZA["balanced"]
    assert envelope["metadata"]["model"] == "xai/grok-4.3"


@pytest.mark.asyncio
async def test_api_dispatch_max_tier_resolves_effort_xhigh() -> None:
    captured: dict[str, Any] = {}

    async def _post(_path: str, *, json: dict[str, Any]) -> MagicMock:
        captured["model"] = json["model"]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "grokbuild.api_dispatch.make_async_client",
        return_value=client,
    ):
        envelope = await api_dispatch_op(
            cwd="/tmp",
            prompt="x",
            system_context=None,
            model=None,
            session_id=None,
            tier="max",
        )

    assert envelope["status"] == "completed"
    assert captured["model"] == _XAI_GROK43_EFFORT_STANZA["max"]
    assert envelope["metadata"]["model"] == "xai/grok-4.3"
    assert default_model_for_tier("max") == "xai/grok-4.3__effort_xhigh"


@pytest.mark.asyncio
async def test_api_dispatch_events_carry_effective_model_stanza(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """A2: apidispatch.* events expose routing stanza as effective_model; model is base."""

    async def _post(_path: str, *, json: dict[str, Any]) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "grokbuild.api_dispatch.make_async_client",
        return_value=client,
    ):
        await api_dispatch_op(
            cwd="/tmp",
            prompt="x",
            system_context=None,
            model=None,
            session_id=None,
            tier="balanced",
            dispatch_id="evt-eff",
        )

    called = [(s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.called"]
    assert len(called) == 1
    assert called[0][1]["model"] == "xai/grok-4.3"
    assert called[0][1]["effective_model"] == _XAI_GROK43_EFFORT_STANZA["balanced"]


@pytest.mark.asyncio
async def test_api_dispatch_bad_tier_returns_rejected_envelope() -> None:
    envelope = await api_dispatch_op(
        cwd="/tmp",
        prompt="x",
        system_context=None,
        model=None,
        session_id=None,
        tier="bogus",
    )
    assert envelope["status"] == "rejected"
    assert envelope["metadata"]["reason_code"] == "bad_tier"
    assert "bogus" in envelope["metadata"]["reason"]


@pytest.mark.asyncio
async def test_api_dispatch_emits_called_and_completed_with_usage(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """F3 + F7: success path emits apidispatch.called → apidispatch.completed
    with token-usage fields parsed from the OpenAI-compatible usage block."""

    async def _post(_path: str, *, json: dict[str, Any]) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 123,
                "completion_tokens": 45,
                "total_tokens": 168,
                "reasoning_tokens": 9876,
            },
        }
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "grokbuild.api_dispatch.make_async_client",
        return_value=client,
    ):
        envelope = await api_dispatch_op(
            cwd="/tmp",
            prompt="x",
            system_context=None,
            model=None,
            session_id=None,
            tier="max",
            dispatch_id="evt-1",
        )

    assert envelope["status"] == "completed"

    called = [(s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.called"]
    completed = [
        (s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.completed"
    ]
    assert len(called) == 1
    assert len(completed) == 1

    assert called[0][1]["dispatch_id"] == "evt-1"
    assert called[0][1]["tier"] == "max"
    assert called[0][1]["model"] == "xai/grok-4.3"
    assert called[0][1]["effective_model"] == _XAI_GROK43_EFFORT_STANZA["max"]

    payload = completed[0][1]
    assert payload["dispatch_id"] == "evt-1"
    assert payload["tier"] == "max"
    assert payload["model"] == "xai/grok-4.3"
    assert payload["effective_model"] == _XAI_GROK43_EFFORT_STANZA["max"]
    assert payload["prompt_tokens"] == 123
    assert payload["completion_tokens"] == 45
    assert payload["total_tokens"] == 168
    assert payload["reasoning_tokens"] == 9876
    assert payload["duration_s"] >= 0.0


@pytest.mark.asyncio
async def test_api_dispatch_bad_tier_emits_rejected_event(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """F3: admission-phase bad_tier emits apidispatch.rejected with reason_code,
    NOT apidispatch.called (admission failed before HTTP post)."""
    await api_dispatch_op(
        cwd="/tmp",
        prompt="x",
        system_context=None,
        model=None,
        session_id=None,
        tier="bogus",
        dispatch_id="evt-2",
    )

    rejected = [(s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.rejected"]
    called = [(s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.called"]

    assert len(rejected) == 1
    assert called == []  # admission failed; no .called emitted

    payload = rejected[0][1]
    assert payload["dispatch_id"] == "evt-2"
    assert payload["reason_code"] == "bad_tier"
    assert "bogus" in payload["reason"]
    assert payload["tier"] == "bogus"


@pytest.mark.asyncio
async def test_api_dispatch_completed_with_missing_usage_block_defaults_to_zero(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """F7: response without a usage block still emits apidispatch.completed,
    with all token fields defaulting to 0. Models that don't surface usage
    must not break the event payload contract."""

    async def _post(_path: str, *, json: dict[str, Any]) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "grokbuild.api_dispatch.make_async_client",
        return_value=client,
    ):
        await api_dispatch_op(
            cwd="/tmp",
            prompt="x",
            system_context=None,
            model=None,
            session_id=None,
            tier="quick",
        )

    completed = [
        (s, p) for s, p in event_log if s == "mcp.grokbuild.apidispatch.completed"
    ]
    assert len(completed) == 1
    payload = completed[0][1]
    assert payload["prompt_tokens"] == 0
    assert payload["completion_tokens"] == 0
    assert payload["total_tokens"] == 0
    assert payload["reasoning_tokens"] == 0
