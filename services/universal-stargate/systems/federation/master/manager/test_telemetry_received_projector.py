"""B-PRIME: manager-owned federation.telemetry.received projection."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from src.scheduling.events.federation_signaling import (  # noqa: E402
    FEDERATION_TELEMETRY_RECEIVED,
)
from systems.federation.common.protocol import FederationMessageType  # noqa: E402
from systems.federation.master.manager.federated_gateway_manager import (  # noqa: E402
    FederatedGatewayManager,
)

_REMOTE = "edge-jupiter"
_GATEWAY = "edge-jupiter-gateway"
_MODEL = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid"


class _EventCapture:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.published.append(event)


def _source() -> dict[str, str]:
    return {
        "stargate_id": _REMOTE,
        "gateway_id": _GATEWAY,
        "node_id": "node-1",
    }


def _telemetry_received(bus: _EventCapture) -> list[Any]:
    return [e for e in bus.published if e.signal == FEDERATION_TELEMETRY_RECEIVED]


async def _drain_tasks() -> None:
    await asyncio.sleep(0.05)


async def _started_manager() -> FederatedGatewayManager:
    bus = _EventCapture()
    mgr = FederatedGatewayManager(bus)
    await mgr.start()
    return mgr


@pytest.mark.asyncio
async def test_ws_resource_update_uses_authoritative_loaded_models() -> None:
    manager = await _started_manager()
    bus = manager._event_bus
    assert isinstance(bus, _EventCapture)

    await manager.update_from_event(
        _REMOTE,
        FederationMessageType.MODEL_LOADED.value,
        {"model_id": _MODEL, "source": _source()},
    )
    await _drain_tasks()
    bus.published.clear()

    await manager.update_from_event(
        _REMOTE,
        FederationMessageType.RESOURCE_UPDATE.value,
        {
            "source": _source(),
            "available_vram_mb": 4096,
            "available_ram_mb": 8192,
            "loaded_models": [],
        },
    )
    await _drain_tasks()

    received = _telemetry_received(bus)
    assert len(received) == 1
    payload = received[0].payload
    assert payload["msg_type"] == FederationMessageType.RESOURCE_UPDATE.value
    assert payload["loaded_model_count"] == 1
    assert payload["count_source"] == "authoritative_loaded_models"
    await manager.stop()


@pytest.mark.asyncio
async def test_http_snapshot_reports_catalog_count() -> None:
    manager = await _started_manager()
    bus = manager._event_bus
    assert isinstance(bus, _EventCapture)

    await manager.apply_snapshot(
        _GATEWAY,
        {
            "sequence_number": 1,
            "available_models": [_MODEL, "other-model"],
            "available_vram_mb": 2048,
            "available_ram_mb": 4096,
        },
        remote_stargate_id=_REMOTE,
    )
    await _drain_tasks()

    received = _telemetry_received(bus)
    assert len(received) == 1
    payload = received[0].payload
    assert payload["msg_type"] == FederationMessageType.GATEWAY_SNAPSHOT.value
    assert payload["catalog_model_count"] == 2
    assert payload["count_source"] == "authoritative_available_models"
    await manager.stop()


@pytest.mark.asyncio
async def test_http_delta_unload_reports_zero_loaded() -> None:
    manager = await _started_manager()
    bus = manager._event_bus
    assert isinstance(bus, _EventCapture)

    await manager.apply_snapshot(
        _GATEWAY,
        {
            "sequence_number": 1,
            "available_models": [_MODEL],
            "loaded_models": [_MODEL],
            "available_vram_mb": 2048,
            "available_ram_mb": 4096,
        },
        remote_stargate_id=_REMOTE,
    )
    await _drain_tasks()
    bus.published.clear()

    await manager.apply_delta(
        _GATEWAY,
        {"loaded_models": []},
        sequence_number=2,
        remote_stargate_id=_REMOTE,
    )
    await _drain_tasks()

    received = _telemetry_received(bus)
    assert len(received) == 1
    payload = received[0].payload
    assert payload["loaded_model_count"] == 0
    assert payload["count_source"] == "authoritative_loaded_models"
    await manager.stop()


@pytest.mark.asyncio
async def test_http_heartbeat_skips_telemetry_received() -> None:
    manager = await _started_manager()
    bus = manager._event_bus
    assert isinstance(bus, _EventCapture)

    await manager.apply_delta(
        _GATEWAY,
        {},
        sequence_number=-1,
        remote_stargate_id="http_poller_heartbeat",
    )
    await _drain_tasks()

    assert _telemetry_received(bus) == []
    await manager.stop()
