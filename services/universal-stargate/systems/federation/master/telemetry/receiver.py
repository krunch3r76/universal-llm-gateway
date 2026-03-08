"""
Telemetry receiver for Master mode.

Receives telemetry from connected Remotes and dispatches to callback.

INVARIANT: Only telemetry types are forwarded to callback
INVARIANT: Non-telemetry types are logged and dropped
"""

from collections.abc import Awaitable, Callable
from typing import Any

from universal_event_bus import EventBus
from universal_logging import get_logger

from ...common.protocol import is_telemetry_type
from ...common.protocol.message import FederationMessageType

logger = get_logger(__name__)


class MasterTelemetryReceiver:
    """
    Receives telemetry from Remote Stargates.

    Validates signal types and dispatches to callback.

    Usage:
        receiver = MasterTelemetryReceiver(
            on_telemetry=manager.update_from_event,
            event_bus=event_bus,
        )
        await receiver.handle_message(remote_id, msg_type, data)
    """

    def __init__(
        self,
        on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        event_bus: EventBus | None = None,
    ):
        """
        Initialize telemetry receiver.

        Args:
            on_telemetry: Callback(remote_id, msg_type, data)
            event_bus: Optional event bus for telemetry received events
        """
        self._on_telemetry: Callable[[str, str, dict[str, Any]], Awaitable[None]] = (
            on_telemetry
        )
        self._event_bus: EventBus | None = event_bus

    async def handle_message(
        self,
        remote_id: str,
        msg_type: str,
        data: dict[str, Any],
    ) -> None:
        """
        Handle incoming message from Remote.

        Filters to telemetry message types only.

        Args:
            remote_id: Identifier of the Remote Stargate
            msg_type: Message type
            data: Message data
        """
        logger.debug(
            f"MasterTelemetryReceiver.handle_message: "
            f"remote_id={remote_id}, type={msg_type}"
        )
        if not is_telemetry_type(msg_type):
            logger.debug(f"Non-telemetry message from {remote_id}: {msg_type}")
            return

        # Request-scoped telemetry: publish to event bus, not gateway manager
        if msg_type == FederationMessageType.REQUEST_INFERENCE_STARTED.value:
            if self._event_bus is not None:
                import asyncio

                from src.scheduling.events import RequestInferenceStarted

                request_id = data.get("request_id")
                model_id = data.get("model_id")
                if request_id and model_id:
                    asyncio.create_task(
                        self._event_bus.publish_async_nowait(
                            RequestInferenceStarted(
                                request_id=request_id,
                                model_id=model_id,
                                gateway_url=data.get("gateway_url", "unknown"),
                                correlation_id=data.get("correlation_id"),
                            )
                        )
                    )
            return

        logger.info(f"Processing telemetry from {remote_id}: {msg_type}")

        # Emit telemetry received event
        if self._event_bus:
            import asyncio

            from src.scheduling.events import FederationTelemetryReceived

            # Extract model count and resource summary from telemetry data
            model_count = 0
            resource_summary: dict[str, Any] = {}

            # GATEWAY_SNAPSHOT: full catalog + resources
            if msg_type == FederationMessageType.GATEWAY_SNAPSHOT.value:
                available_models = data.get("available_models", [])
                if isinstance(available_models, list):
                    model_count = len(available_models)
                resource_summary = {
                    "available_vram_mb": data.get("available_vram_mb", 0),
                    "available_ram_mb": data.get("available_ram_mb", 0),
                }

            # RESOURCE_UPDATE: resources + loaded model count
            elif msg_type == FederationMessageType.RESOURCE_UPDATE.value:
                loaded_models = data.get("loaded_models", [])
                if isinstance(loaded_models, list):
                    model_count = len(loaded_models)
                resource_summary = {
                    "available_vram_mb": data.get("available_vram_mb", 0),
                    "available_ram_mb": data.get("available_ram_mb", 0),
                }

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    FederationTelemetryReceived(
                        remote_id=remote_id,
                        model_count=model_count,
                        resource_summary=resource_summary,
                    )
                )
            )

        await self._on_telemetry(remote_id, msg_type, data)
