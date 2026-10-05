"""Connected flag and refresh interval when catalog fetch fails or succeeds."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from systems.cursor_catalog.config import CursorSdkCatalogConfig
from systems.cursor_catalog.registry import (
    _GATEWAY_ID,
    _REFRESH_INTERVAL_S,
    _RETRY_INTERVAL_S,
    CursorSdkCatalogPoller,
)


@pytest.mark.asyncio
async def test_health_ok_catalog_500_keeps_retry_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health 200 with catalog 500 → not connected, gateway removed, 30s wait."""
    intervals: list[float] = []
    sleep_calls = 0

    async def fake_sleep(interval: float) -> None:
        nonlocal sleep_calls
        intervals.append(interval)
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    manager = MagicMock()
    manager.remove_gateways = AsyncMock()
    manager.register_cursor_gateway = AsyncMock()

    poller = CursorSdkCatalogPoller(
        CursorSdkCatalogConfig(worker_url="http://127.0.0.1:8091"),
        manager,
    )
    poller._connected = False
    poller._probe_health = AsyncMock(return_value=True)
    poller._fetch_catalog = AsyncMock(return_value=[])

    try:
        with pytest.raises(asyncio.CancelledError):
            await poller._refresh_loop()
    finally:
        await poller._client.aclose()

    assert intervals[0] == _RETRY_INTERVAL_S
    assert intervals[1] == _RETRY_INTERVAL_S
    assert poller._connected is False
    manager.remove_gateways.assert_awaited_with([_GATEWAY_ID])
    manager.register_cursor_gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_fetch_sets_connected_and_refresh_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After models register, connected is true and the loop waits 3600s."""
    intervals: list[float] = []
    sleep_calls = 0

    async def fake_sleep(interval: float) -> None:
        nonlocal sleep_calls
        intervals.append(interval)
        sleep_calls += 1
        if sleep_calls >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    manager = MagicMock()
    manager.remove_gateways = AsyncMock()
    manager.register_cursor_gateway = AsyncMock()
    manager.get_gateway = MagicMock(return_value=None)

    poller = CursorSdkCatalogPoller(
        CursorSdkCatalogConfig(worker_url="http://127.0.0.1:8091"),
        manager,
    )
    poller._connected = False
    poller._probe_health = AsyncMock(return_value=True)
    poller._fetch_catalog = AsyncMock(
        side_effect=[
            [],
            [{"cursor_id": "cursor/composer-2.5"}],
        ]
    )
    poller._emit_available = AsyncMock()
    poller._emit_catalog_updated = AsyncMock()
    poller._emit_drift_if_needed = AsyncMock()

    try:
        with pytest.raises(asyncio.CancelledError):
            await poller._refresh_loop()
    finally:
        await poller._client.aclose()

    assert intervals[0] == _RETRY_INTERVAL_S
    assert intervals[1] == _RETRY_INTERVAL_S
    assert intervals[2] == _REFRESH_INTERVAL_S
    assert poller._connected is True
    manager.register_cursor_gateway.assert_awaited()
    poller._emit_available.assert_awaited()


@pytest.mark.asyncio
async def test_startup_does_not_connect_on_catalog_http_error() -> None:
    """Startup health OK but catalog HTTP error must not set connected."""
    manager = MagicMock()
    manager.remove_gateways = AsyncMock()
    manager.register_cursor_gateway = AsyncMock()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            500 if request.url.path.endswith("/catalog") else 200
        )
    )
    poller = CursorSdkCatalogPoller(
        CursorSdkCatalogConfig(worker_url="http://127.0.0.1:8091"),
        manager,
    )
    await poller._client.aclose()
    poller._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8091",
        transport=transport,
    )
    poller._refresh_task = asyncio.create_task(asyncio.sleep(3600))
    poller._heartbeat_task = asyncio.create_task(asyncio.sleep(3600))
    poller._emit_available = AsyncMock()

    try:
        reachable = await poller._probe_health()
        assert reachable is True
        poller._connected = await poller._fetch_and_register()
        assert poller._connected is False
        manager.remove_gateways.assert_awaited_with([_GATEWAY_ID])
        poller._emit_available.assert_not_awaited()
    finally:
        poller._refresh_task.cancel()
        poller._heartbeat_task.cancel()
        await poller._client.aclose()
