"""Stargate worker POST carries Stargate request_id when dispatch_id is a uuid4."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
)

_MINTED_DISPATCH_ID = "550e8400-e29b-41d4-a716-446655440000"
_STARGATE_REQUEST_ID = "rid"


def _patch_client(monkeypatch: pytest.MonkeyPatch, captured: list[dict[str, object]]) -> None:
    async def _post(_url: str, *, json: dict[str, object]) -> MagicMock:
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {"admitted": True}
        return resp

    client = AsyncMock()
    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.worker_base_url",
        lambda: "http://worker.test",
    )


@pytest.mark.asyncio
async def test_worker_packet_dispatch_forwards_request_id_with_uuid_dispatch_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    _patch_client(monkeypatch, captured)

    ok, _detail = await dispatch_cursor_sdk_worker(
        request_id=_STARGATE_REQUEST_ID,
        thread_id="thread-1",
        model="composer-2.5",
        execution_id="exec-1",
        packet_path="tmp/packet.md",
        handoff_contract="implement",
        dispatch_id=_MINTED_DISPATCH_ID,
    )
    assert ok is True
    assert captured[0]["request_id"] == _STARGATE_REQUEST_ID
    assert captured[0]["dispatch_id"] == _MINTED_DISPATCH_ID


@pytest.mark.asyncio
async def test_worker_message_dispatch_forwards_request_id_with_uuid_dispatch_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    _patch_client(monkeypatch, captured)

    ok, _detail = await dispatch_cursor_sdk_worker_message(
        request_id=_STARGATE_REQUEST_ID,
        thread_id="thread-1",
        model="composer-2.5",
        message="hello",
        execution_id="exec-1",
        handoff_contract="none",
        dispatch_id=_MINTED_DISPATCH_ID,
    )
    assert ok is True
    assert captured[0]["request_id"] == _STARGATE_REQUEST_ID
    assert captured[0]["dispatch_id"] == _MINTED_DISPATCH_ID
