"""
Resource update consumer for tracking gateway resource availability.

Subscribes to GATEWAY_RESOURCE_UPDATE events to maintain real-time
resource state without periodic polling.
"""

from typing import TYPE_CHECKING

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import GATEWAY_RESOURCE_UPDATE

if TYPE_CHECKING:
    from gateways import SingleGatewayManager

logger = get_logger(__name__)


class ResourceUpdateConsumer:
    """
    Consumes gateway resource update events.

    Maintains real-time view of gateway resources (RAM/VRAM availability,
    loaded models, busy models) without periodic polling.
    """

    def __init__(
        self,
        event_bus: EventBus,
        gateway_manager: "SingleGatewayManager",
    ):
        self.event_bus = event_bus
        self.gateway_manager = gateway_manager

    def start(self) -> None:
        """Start consuming resource update events."""
        self.event_bus.subscribe_async(
            GATEWAY_RESOURCE_UPDATE, self._handle_resource_update
        )
        logger.info("✅ ResourceUpdateConsumer started")

    def stop(self) -> None:
        """Stop consuming events."""
        logger.info("ResourceUpdateConsumer stopped")

    async def _handle_resource_update(self, event: Event) -> None:
        """Handle GATEWAY_RESOURCE_UPDATE event."""
        payload = event.payload
        gateway_url = payload.get("url")

        if not gateway_url:
            logger.warning(f"Invalid GATEWAY_RESOURCE_UPDATE payload: {payload}")
            return

        self._update_gateway_resources(gateway_url, payload)

    def _update_gateway_resources(self, gateway_url: str, payload: dict) -> None:
        """Update gateway resource state from event payload."""
        resource_state = {
            "total_vram_mb": payload.get("total_vram_mb", 0),
            "available_vram_mb": payload.get("available_vram_mb", 0),
            "total_ram_mb": payload.get("total_ram_mb", 0),
            "available_ram_mb": payload.get("available_ram_mb", 0),
            "loaded_models": set(payload.get("loaded_models", [])),
            "busy_models": set(payload.get("busy_models", [])),
        }

        # Ensure resource cache exists
        if not hasattr(self.gateway_manager, "_resource_cache"):
            self.gateway_manager._resource_cache = {}

        self.gateway_manager._resource_cache[gateway_url] = resource_state

        vram = (
            f"{resource_state['available_vram_mb']}/"
            f"{resource_state['total_vram_mb']}"
        )
        ram = (
            f"{resource_state['available_ram_mb']}/"
            f"{resource_state['total_ram_mb']}"
        )
        loaded = list(resource_state["loaded_models"]) or "NONE"
        logger.info(
            f"📊 Updated resource cache for {gateway_url}: "
            f"VRAM={vram}MB, RAM={ram}MB, loaded_models={loaded}"
        )
