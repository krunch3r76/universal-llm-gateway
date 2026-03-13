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

        # Emit telemetry received event with disambiguation fields
        from src.scheduling.events import FederationTelemetryReceived

        model_count = 0
        resource_summary: dict[str, Any] = {}
        catalog_model_count: int | None = None
        loaded_model_count: int | None = None
        msg_type: str

        if response.update_type == "snapshot":
            msg_type = "GATEWAY_SNAPSHOT"
            state = response.data.get("state", response.data)
            available = state.get("available_models", [])
            catalog_model_count = len(available) if isinstance(available, list) else 0
            model_count = catalog_model_count
            count_source = "snapshot_available_models"
            resource_summary = {
                "available_vram_mb": state.get(
                    "available_vram_mb",
                    state.get("vram_free_mb", 0),
                ),
                "available_ram_mb": state.get(
                    "available_ram_mb",
                    state.get("ram_free_mb", 0),
                ),
            }
        else:
            msg_type = "RESOURCE_UPDATE"
            changes = response.data.get("changes", {})
            loaded = changes.get("loaded_models", [])
            loaded_model_count = len(loaded) if isinstance(loaded, list) else 0
            model_count = loaded_model_count
            count_source = "message_loaded_models"
            resource_summary = {
                "available_vram_mb": changes.get(
                    "available_vram_mb",
                    changes.get("vram_free_mb", 0),
                ),
                "available_ram_mb": changes.get(
                    "available_ram_mb",
                    changes.get("ram_free_mb", 0),
                ),
            }

        asyncio.create_task(
            self._event_bus.publish_async_nowait(
                FederationTelemetryReceived(
                    remote_id=response.remote_stargate_id,
                    model_count=model_count,
                    resource_summary=resource_summary,
                    msg_type=msg_type,
                    catalog_model_count=catalog_model_count,
                    loaded_model_count=loaded_model_count,
                    count_source=count_source,
                )
            )
        )

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

        if changes or response.data.get("critical_events"):
            await self._gateway_manager.apply_delta(
                response.gateway_id,
                changes,
                response.sequence_number,
                remote_stargate_id=response.remote_stargate_id,
            )

            # Process critical events
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
            self._event_bus.publish_async_nowait(
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
                self._event_bus.publish_async_nowait(
                    FederationModelLoaded(
                        gateway_id=gateway_id,
                        model_id=ModelId.parse(model_id),
                    )
                )
            )
        elif event_type == "MODEL_UNLOADED":
            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    FederationModelUnloaded(
                        gateway_id=gateway_id,
                        model_id=ModelId.parse(model_id),
                    )
                )
            )
