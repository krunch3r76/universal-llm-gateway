"""
Telemetry delta/snapshot application to gateway state.

INVARIANT: ∀ telemetry_receipt ⟹ GATEWAY_RESOURCE_UPDATE published
INVARIANT: Timestamps updated even for empty deltas (heartbeat)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

from src.scheduling.events import (
    FederationGatewayResourceUpdateSignal,
    FederationModelLoaded,
    FederationModelUnloaded,
)

from .fetcher import TelemetryResponse

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from ....master.manager.federated_gateway_manager import FederatedGatewayManager

logger = get_logger(__name__)


class TelemetryApplicator:
    """
    Applies telemetry updates to gateway manager and publishes events.

    CRITICAL: Must publish GATEWAY_RESOURCE_UPDATE for FreshnessWaiter integration.
    """

    def __init__(
        self,
        gateway_manager: FederatedGatewayManager,
        event_bus: EventBus,
    ):
        self._gateway_manager = gateway_manager
        self._event_bus = event_bus

    async def apply(self, response: TelemetryResponse) -> None:
        """
        Apply telemetry update and publish events.

        INVARIANT: Always publishes GATEWAY_RESOURCE_UPDATE after apply
        """
        if response.update_type == "snapshot":
            await self._apply_snapshot(response)
        else:
            await self._apply_delta(response)

        # CRITICAL: Notify FreshnessWaiter via GATEWAY_RESOURCE_UPDATE
        await self._publish_resource_update(response.gateway_id)

    async def _apply_snapshot(self, response: TelemetryResponse) -> None:
        """Apply full snapshot."""
        await self._gateway_manager.apply_snapshot(
            response.gateway_id,
            response.data.get("state", response.data),
            remote_stargate_id=response.remote_stargate_id,
        )
        logger.info(
            f"Applied snapshot from {response.remote_stargate_id} "
            f"(gateway: {response.gateway_id})"
        )

    async def _apply_delta(self, response: TelemetryResponse) -> None:
        """Apply delta changes."""
        changes = response.data.get("changes", {})

        await self._gateway_manager.apply_delta(
            response.gateway_id,
            changes,
            response.sequence_number,
            remote_stargate_id=response.remote_stargate_id,
        )

        if changes or response.data.get("critical_events"):
            await self._publish_critical_events(response)

            logger.info(
                f"Applied delta from {response.remote_stargate_id}: "
                f"gateway={response.gateway_id}, seq={response.sequence_number}"
            )

    async def send_heartbeat(self, gateway_id: str | None) -> None:
        """
        Send heartbeat update for 204 No Content responses.

        Keeps gateway timestamp fresh even when no changes.

        Args:
            gateway_id: Gateway identifier to mark as alive.
        """
        if not gateway_id:
            return

        await self._gateway_manager.apply_delta(
            gateway_id,
            delta={},
            sequence_number=-1,  # Sentinel for heartbeat
            remote_stargate_id="http_poller_heartbeat",
        )

        # Still notify FreshnessWaiter (timestamp updated)
        await self._publish_resource_update(gateway_id)

    async def _publish_resource_update(self, gateway_id: str) -> None:
        """
        Publish GATEWAY_RESOURCE_UPDATE for FreshnessWaiter integration.

        CRITICAL: This enables epoch-based waiting for load completion.
        Without this, HTTP polling telemetry won't trigger FreshnessWaiter.
        """
        # Fire-and-forget
        asyncio.create_task(
            self._event_bus.publish_nowait(
                FederationGatewayResourceUpdateSignal(
                    gateway_id=gateway_id,
                    source="http_polling",
                )
            )
        )

    async def _publish_critical_events(self, response: TelemetryResponse) -> None:
        """Publish critical events from telemetry response."""
        for event in response.data.get("critical_events", []):
            await self._handle_critical_event(response.gateway_id, event)

    async def _handle_critical_event(
        self,
        gateway_id: str,
        event: dict[str, Any],
    ) -> None:
        """Handle critical event by publishing to EventBus."""
        event_type = event.get("event")
        model_id = event.get("model_id")

        if not model_id:
            return

        if event_type == "MODEL_LOADED":
            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationModelLoaded(
                        gateway_id=gateway_id,
                        model_id=ModelId.parse(model_id),
                    )
                )
            )
        elif event_type == "MODEL_UNLOADED":
            asyncio.create_task(
                self._event_bus.publish_nowait(
                    FederationModelUnloaded(
                        gateway_id=gateway_id,
                        model_id=ModelId.parse(model_id),
                    )
                )
            )
