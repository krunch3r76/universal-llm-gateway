"""Tests for GIW work-registry record relay payloads."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from systems.frontier_consult.work_registry_admit_relay import (
    relay_registry_heartbeat,
    relay_registry_record,
)


@pytest.mark.asyncio
async def test_relay_registry_record_includes_max_wall_s_when_set() -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"ok": True, "registry_row_id": "x"}

    async def _post(_url: str, *, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp()

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.frontier_consult.work_registry_admit_relay.make_async_client",
        return_value=mock_client,
    ):
        await relay_registry_record(
            request_id="req-1",
            dispatch_id="disp-1",
            thread_id="21802",
            max_wall_s=43200,
        )
    assert captured["max_wall_s"] == 43200


@pytest.mark.asyncio
async def test_relay_registry_record_omits_max_wall_s_when_none() -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"ok": True, "registry_row_id": "x"}

    async def _post(_url: str, *, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp()

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.frontier_consult.work_registry_admit_relay.make_async_client",
        return_value=mock_client,
    ):
        await relay_registry_record(
            request_id="req-2",
            dispatch_id="disp-2",
            thread_id="21802",
        )
    assert "max_wall_s" not in captured


@pytest.mark.asyncio
async def test_relay_registry_heartbeat_posts_dispatch_id() -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"ok": True, "bumped": True}

    async def _post(_url: str, *, json: dict[str, Any]) -> _Resp:
        captured["url"] = _url
        captured.update(json)
        return _Resp()

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.frontier_consult.work_registry_admit_relay.make_async_client",
        return_value=mock_client,
    ):
        await relay_registry_heartbeat(
            request_id="req-hb",
            dispatch_id="exec-hb-1",
        )
    assert captured["url"] == "/api/v1/cursor/registry/heartbeat"
    assert captured["dispatch_id"] == "exec-hb-1"
