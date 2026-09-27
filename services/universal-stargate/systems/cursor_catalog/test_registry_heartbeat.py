"""Poller liveness heartbeat — independent of hourly catalog refresh."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from model_id import ModelId

from systems.cursor_catalog.config import CursorSdkCatalogConfig
from systems.cursor_catalog.registry import (
    _GATEWAY_ID,
    _HEARTBEAT_INTERVAL_S,
    CursorSdkCatalogPoller,
)
from systems.federation.common.types import FederatedGateway


def _catalog_gateway(last_heartbeat: float) -> FederatedGateway:
    return FederatedGateway(
        gateway_id=_GATEWAY_ID,
        remote_stargate_id="cursor-sdk",
        remote_stargate_url="http://127.0.0.1:8091",
        backend_type="cursor_sdk",
        provider_name="cursor",
        available_models=frozenset({ModelId.parse("cursor/composer-2.5")}),
        dispatchable=False,
        last_heartbeat=last_heartbeat,
        telemetry_timestamp=last_heartbeat,
    )


@pytest.mark.asyncio
async def test_heartbeat_stays_fresh_past_catalog_refresh_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 300s virtual time, last_heartbeat age stays ≤120s while connected."""
    clock = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: clock)

    stored: dict[str, FederatedGateway] = {}

    async def register_cursor_gateway(gateway: FederatedGateway) -> None:
        stored["gw"] = gateway

    manager = MagicMock()
    manager.get_gateway = lambda gateway_id: stored.get("gw")
    manager.register_cursor_gateway = AsyncMock(side_effect=register_cursor_gateway)

    poller = CursorSdkCatalogPoller(
        CursorSdkCatalogConfig(worker_url="http://127.0.0.1:8091"),
        manager,
    )
    initial_hb = clock
    stored["gw"] = _catalog_gateway(initial_hb)
    poller._connected = True

    async def fake_sleep(interval: float) -> None:
        nonlocal clock
        assert interval == _HEARTBEAT_INTERVAL_S
        clock += interval
        if clock >= initial_hb + 301:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await poller._heartbeat_loop()
    except asyncio.CancelledError:
        pass
    finally:
        await poller._client.aclose()

    final = stored["gw"]
    assert final.last_heartbeat > initial_hb
    age_s = clock - final.last_heartbeat
    assert age_s <= 120
