"""
Telemetry receiver for Master mode.

Receives telemetry from connected Remotes and dispatches to callback.

INVARIANT: Only telemetry types are forwarded to callback
INVARIANT: Non-telemetry types are logged and dropped
"""

import asyncio
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
                from src.scheduling.events import RequestInferenceStarted

                request_id = data.get("request_id")
                model_id = data.get("model_id")
                if request_id and model_id:
                    asyncio.create_task(
                        self._event_bus.publish_nowait(
                            RequestInferenceStarted(
                                request_id=request_id,
                                model_id=str(model_id),
                                gateway_url=data.get("gateway_url", "unknown"),
                                correlation_id=data.get("correlation_id"),
                            )
                        )
                    )
            return

        logger.info(f"Processing telemetry from {remote_id}: {msg_type}")

        await self._on_telemetry(remote_id, msg_type, data)
