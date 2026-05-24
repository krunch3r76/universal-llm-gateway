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
    assert envelope["metadata"]["model"] == _XAI_GROK43_EFFORT_STANZA["balanced"]


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
    assert default_model_for_tier("max") == "xai/grok-4.3__effort_xhigh"


@pytest.mark.asyncio
async def test_api_dispatch_bad_tier_returns_failed_envelope() -> None:
    envelope = await api_dispatch_op(
        cwd="/tmp",
        prompt="x",
        system_context=None,
        model=None,
        session_id=None,
        tier="bogus",
    )
    assert envelope["status"] == "failed"
    assert envelope["metadata"]["reason_code"] == "bad_tier"
    assert "bogus" in envelope["metadata"]["reason"]
