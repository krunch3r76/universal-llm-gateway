"""Unit tests for the federation liveness watchdog sweep."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from src.scheduling.events.federation_signaling import (  # noqa: E402
    FEDERATION_GATEWAY_LIVENESS_STALE,
    FEDERATION_GATEWAY_RECOVERED,
)
from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.federation.master.circuit_breaker import (  # noqa: E402
    FederationCircuitBreaker,
)
from systems.federation.master.manager.federated_gateway_manager import (  # noqa: E402
    FederatedGatewayManager,
)

_GATEWAY = "edge-jupiter-gateway"
_REMOTE = "edge-jupiter"


class _EventCapture:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.published.append(event)


def _gateway(
    *,
    gateway_id: str = _GATEWAY,
    backend_type: str = "federated",
    last_heartbeat: float,
) -> FederatedGateway:
    return FederatedGateway(
        gateway_id=gateway_id,
        remote_stargate_id=_REMOTE,
        remote_stargate_url="http://edge",
        backend_type=backend_type,
        last_heartbeat=last_heartbeat,
        telemetry_timestamp=last_heartbeat,
    )


async def _drain_tasks() -> None:
    await asyncio.sleep(0.05)


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> float:
    now = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    return now


@pytest.mark.asyncio
async def test_stale_gateway_emits_once(frozen_now: float) -> None:
    bus = _EventCapture()
    breaker = FederationCircuitBreaker(event_bus=bus)
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._liveness_threshold_ms = 300_000
    manager._gateways[_GATEWAY] = _gateway(
        last_heartbeat=frozen_now - 400,
    )

    manager._sweep_liveness()
    await _drain_tasks()

    stale = [e for e in bus.published if e.signal == FEDERATION_GATEWAY_LIVENESS_STALE]
    assert len(stale) == 1
    payload = stale[0].payload
    assert payload["gateway_id"] == _GATEWAY
    assert payload["threshold_ms"] == 300_000
    assert payload["backend_type"] == "federated"
    assert _GATEWAY in manager._liveness_alerted


@pytest.mark.asyncio
async def test_second_sweep_while_stale_does_not_re_emit(frozen_now: float) -> None:
    bus = _EventCapture()
    breaker = FederationCircuitBreaker(event_bus=bus)
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._gateways[_GATEWAY] = _gateway(last_heartbeat=frozen_now - 400)

    manager._sweep_liveness()
    await _drain_tasks()
    bus.published.clear()

    manager._sweep_liveness()
    await _drain_tasks()

    stale = [e for e in bus.published if e.signal == FEDERATION_GATEWAY_LIVENESS_STALE]
    assert stale == []


@pytest.mark.asyncio
async def test_recovery_emits_liveness_kind(frozen_now: float) -> None:
    bus = _EventCapture()
    breaker = FederationCircuitBreaker(event_bus=bus)
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._gateways[_GATEWAY] = _gateway(last_heartbeat=frozen_now - 400)

    manager._sweep_liveness()
    await _drain_tasks()
    bus.published.clear()

    manager._gateways[_GATEWAY] = _gateway(last_heartbeat=frozen_now - 10)
    manager._sweep_liveness()
    await _drain_tasks()

    recovered = [
        e for e in bus.published if e.signal == FEDERATION_GATEWAY_RECOVERED
    ]
    assert len(recovered) == 1
    payload = recovered[0].payload
    assert payload["kind"] == "liveness"
    assert payload["reason"] == "heartbeat_resumed"
    assert "downtime_ms" in payload
    assert _GATEWAY not in manager._liveness_alerted


@pytest.mark.asyncio
async def test_cloud_gateway_skipped(frozen_now: float) -> None:
    bus = _EventCapture()
    breaker = FederationCircuitBreaker(event_bus=bus)
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._gateways["cloud-gw"] = _gateway(
        gateway_id="cloud-gw",
        backend_type="cloud_api",
        last_heartbeat=frozen_now - 400,
    )

    manager._sweep_liveness()
    await _drain_tasks()

    assert bus.published == []


@pytest.mark.asyncio
async def test_stale_sweep_does_not_mutate_gateway_wide_open(
    frozen_now: float,
) -> None:
    bus = _EventCapture()
    breaker = FederationCircuitBreaker(event_bus=bus)
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._gateways[_GATEWAY] = _gateway(last_heartbeat=frozen_now - 400)

    manager._sweep_liveness()
    await _drain_tasks()

    assert breaker._gateway_wide_open == {}


@pytest.mark.asyncio
async def test_watchdog_loop_survives_sweep_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _EventCapture()
    breaker = MagicMock()
    manager = FederatedGatewayManager(bus, circuit_breaker=breaker)
    manager._liveness_sweep_interval_s = 0.01
    calls = {"count": 0}

    def _boom() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("sweep failed")

    monkeypatch.setattr(manager, "_sweep_liveness", _boom)
    await manager.start()
    await asyncio.sleep(0.08)
    await manager.stop()

    assert calls["count"] >= 2


@pytest.mark.asyncio
async def test_set_circuit_breaker_starts_watchdog_after_start() -> None:
    bus = _EventCapture()
    manager = FederatedGatewayManager(bus)
    breaker = FederationCircuitBreaker(event_bus=bus)
    await manager.start()
    assert manager._liveness_task is None

    manager.set_circuit_breaker(breaker)
    assert manager._liveness_task is not None
    await manager.stop()
