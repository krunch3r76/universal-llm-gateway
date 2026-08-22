"""Cloud / cursor virtual gateways must not copy catalog into loaded_models."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from model_id import ModelId

from systems.cloud.config import CloudProxyConfig
from systems.cloud.registry import CloudProxyCatalogPoller
from systems.cursor_catalog.config import CursorSdkCatalogConfig
from systems.cursor_catalog.registry import CursorSdkCatalogPoller


def test_cloud_virtual_gateway_loaded_models_empty() -> None:
    poller = CloudProxyCatalogPoller(
        CloudProxyConfig(url="http://127.0.0.1:9"),
        MagicMock(),
    )
    mid = ModelId.parse("anthropic/claude-sonnet-5")
    gateway = poller._build_virtual_gateway("anthropic", [mid], max_concurrent=20)
    assert gateway.is_cloud is True
    assert gateway.available_models == frozenset({mid})
    assert gateway.loaded_models == frozenset()


@pytest.mark.asyncio
async def test_cursor_catalog_loaded_models_empty() -> None:
    manager = MagicMock()
    manager.register_cursor_gateway = AsyncMock()
    manager.remove_gateways = AsyncMock()
    poller = CursorSdkCatalogPoller(
        CursorSdkCatalogConfig(worker_url="http://127.0.0.1:9"),
        manager,
    )
    poller._fetch_catalog = AsyncMock(
        return_value=[{"cursor_id": "cursor/composer-2.5"}]
    )
    poller._emit_catalog_updated = AsyncMock()
    poller._emit_drift_if_needed = AsyncMock()
    try:
        await poller._fetch_and_register()
        gateway = manager.register_cursor_gateway.await_args.args[0]
        assert gateway.loaded_models == frozenset()
        assert ModelId.parse("cursor/composer-2.5") in gateway.available_models
        assert gateway.is_cloud is False
    finally:
        await poller._client.aclose()
