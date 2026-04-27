"""
Master-initiated Edge WebSocket telemetry clients.

Starts one client per configured remote where:
  - disable_websocket == False
  - telemetry_ws_initiator == "master"

This is the Golem-friendly topology where Master can reach Edge Stargates directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ..common.config import FederationConfig
from ..common.config.schema import TelemetryWSInitiator
from ..link.ws.edge import MasterEdgeWebSocketClient
from .peer_callbacks import build_peer_callbacks

logger = get_logger(__name__)


class MasterEdgeWSClients:
    """Manages Master→Edge WebSocket telemetry connections."""

    def __init__(
        self,
        *,
        config: FederationConfig,
        on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        event_bus: Any | None = None,
    ) -> None:
        self._config = config
        self._on_telemetry = on_telemetry
        self._event_bus = event_bus
        self._clients: dict[str, MasterEdgeWebSocketClient] = {}
        self._on_connected, self._on_disconnected = build_peer_callbacks(
            event_bus=event_bus,
        )

    async def start(self) -> None:
        """Start clients for remotes that require Master-initiated WS."""
        remotes = [
            r
            for r in self._config.remotes
            if (not r.disable_websocket)
            and r.telemetry_ws_initiator == TelemetryWSInitiator.MASTER
        ]
        if not remotes:
            return

        for remote in remotes:
            if remote.stargate_id in self._clients:
                continue
            client = MasterEdgeWebSocketClient(
                config=self._config,
                remote_config=remote,
                on_telemetry=self._on_telemetry,
                on_connected=self._on_connected,
                on_disconnected=self._on_disconnected,
                event_bus=self._event_bus,
            )
            self._clients[remote.stargate_id] = client
            await client.connect()
            logger.info(f"Started MasterEdgeWebSocketClient for {remote.stargate_id}")

    async def stop(self) -> None:
        """Disconnect all managed clients."""
        if not self._clients:
            return

        async with asyncio.TaskGroup() as tg:
            for client in self._clients.values():
                tg.create_task(client.disconnect())
        self._clients.clear()
