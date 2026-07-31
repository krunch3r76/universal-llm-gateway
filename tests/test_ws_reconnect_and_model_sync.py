"""
Regression tests for Stargate↔Gateway WebSocket resilience.

Focus:
- ConnectionManager reconnect loop must be restartable across multiple failures.
- Coordinator model sync on reconnect must wake in-flight load waiters when a
  model becomes loaded while the WebSocket is down.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Add Stargate service to path
stargate_path = Path(__file__).parent.parent / "services" / "universal-stargate"
if str(stargate_path) not in sys.path:
    sys.path.insert(0, str(stargate_path))

from gateway_websocket.ws_client.connection import ConnectionManager
from systems.proxy.core.control_plane.model_lifecycle.coordination.global_coordinator import (
    GlobalModelLoadCoordinator,
)
from systems.proxy.stargate.runtime.gateway_bootstrap import _build_loaded_model_sync_callback
from model_id import ModelId


@pytest.mark.asyncio
async def test_connection_manager_reconnect_task_is_cleared_when_done(monkeypatch):
    cm = ConnectionManager(
        ws_url="ws://example.invalid/ws/stargate",
        gateway_name="gw-1",
        reconnect_interval=0.01,
        max_reconnect_attempts=1,
        connect_timeout=0.01,
    )

    async def always_fail_connect(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(cm, "connect", always_fail_connect)

    cm.start_reconnect_loop(on_init=lambda _data: None)
    task = cm._reconnect_task
    assert task is not None

    await asyncio.wait_for(task, timeout=1.0)
    assert cm._reconnect_task is None

    await cm.disconnect()


@pytest.mark.asyncio
async def test_loaded_model_sync_wakes_inflight_load_waiters():
    coordinator = GlobalModelLoadCoordinator()
    await coordinator.start()
    try:
        gateway_name = "gw-1"
        model_id = "test-model-4096"
        normalized = ModelId.parse(model_id).normalized

        # Mark model as "loading" in coordinator (creates the wait event).
        decision = await coordinator.request_model_load(normalized, gateway_name)
        assert decision.should_load is True

        # Pipeline path: reserve_for_routing returns a wait event while loading.
        can_reserve, redirect_gateway, wait_event = await coordinator.reserve_for_routing(
            model_id, requester_id="req-1", ttl_seconds=1.0
        )
        assert can_reserve is False
        assert redirect_gateway == gateway_name
        assert wait_event is not None

        # Reconnect snapshot: model is now loaded on gateway. Sync must wake waiters.
        sync_callback = _build_loaded_model_sync_callback(coordinator)
        sync_callback(gateway_name, frozenset({model_id}))

        await asyncio.wait_for(wait_event.wait(), timeout=1.0)

        # After wake, reserve_for_routing should immediately redirect with no wait.
        can_reserve2, redirect_gateway2, wait_event2 = await coordinator.reserve_for_routing(
            model_id, requester_id="req-2", ttl_seconds=1.0
        )
        assert can_reserve2 is False
        assert redirect_gateway2 == gateway_name
        assert wait_event2 is None
    finally:
        await coordinator.stop()


