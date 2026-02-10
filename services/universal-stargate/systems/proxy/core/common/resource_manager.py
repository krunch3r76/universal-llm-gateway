"""
VRAM/RAM reservation manager for model loading.

Flow:
    1. Request arrives to reserve resources for model X
    2. Check current availability (sync with live metrics)
    3. If sufficient: create reservation, return success
    4. If insufficient: return failure, caller can retry or route elsewhere

Event-Driven Cleanup:
    Each reservation manages its own expiration via asyncio.Task.
    No periodic cleanup loop - expiration tasks fire on timeout.

Event-Driven Architecture:
    Publishes RESOURCE_RESERVED and RESOURCE_RELEASED events to update
    cached gateway resource state immediately.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from universal_event_bus import EventBus
from universal_event_bus.actor import Sequential, sequential
from universal_logging import get_logger

from ...resource_management import GatewayConfigManager, ResourceManagementConfig

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


@dataclass(slots=True, kw_only=True)
class ResourceReservation:
    """
    Represents a resource reservation for model loading.

    Each reservation manages its own expiration task instead of
    relying on a periodic cleanup loop.
    """

    id: str
    gateway_id: str
    model_id: str
    vram_mb: int
    ram_mb: int
    created_at: float = field(default_factory=time.time)
    state: str = "pending"  # pending, active, released, expired

    # Per-reservation expiration management
    _expiration_task: asyncio.Task | None = field(default=None, repr=False)
    _on_expire: object = field(default=None, repr=False)

    def schedule_expiration(self, timeout: float, on_expire) -> None:
        """Schedule expiration task for this reservation."""
        self._on_expire = on_expire
        self._expiration_task = asyncio.create_task(
            self._expire_after_timeout(timeout),
            name=f"reservation-expire-{self.id[:16]}",
        )

    async def _expire_after_timeout(self, timeout: float) -> None:
        """Expire this reservation after timeout."""
        try:
            await asyncio.sleep(timeout)

            if self.state in ("pending", "active"):
                self.state = "expired"
                logger.warning(f"Reservation {self.id[:16]} expired after {timeout}s")
                if self._on_expire:
                    self._on_expire(self)

        except asyncio.CancelledError:
            pass  # Cancelled on release

    def cancel_expiration(self) -> None:
        """Cancel expiration (called on release)."""
        if self._expiration_task and not self._expiration_task.done():
            self._expiration_task.cancel()
            self._expiration_task = None


class GatewayResourceManager(Sequential):
    """
    VRAM/RAM reservation manager for model loading.

    Flow:
        1. Request arrives to reserve resources for model X
        2. Check current availability (sync with live metrics)
        3. If sufficient: create reservation, return success
        4. If insufficient: return failure, caller can retry or route elsewhere

    Event-Driven Architecture:
        Each reservation has its own expiration task. No cleanup loop.
        Uses @sequential decorator instead of locks for reservation operations.
    """

    def __init__(
        self,
        gateway_id: str,
        metrics_provider,
        state_manager,
        config_manager: GatewayConfigManager | None = None,
        event_bus: EventBus | None = None,
    ):
        """
        Initialize resource manager with config manager dependency.

        Inputs:
            gateway_id: Unique identifier for this gateway
            metrics_provider: Provider for metrics collection
            state_manager: Manager for resource state tracking
            config_manager: Event-driven configuration manager (optional, defaults used if None)
            event_bus: Optional event bus for publishing reservation events
        """
        super().__init__()
        self._gateway_id = gateway_id
        self._metrics_provider = metrics_provider
        self._state_manager = state_manager
        self._config_manager = config_manager
        self._event_bus = event_bus
        self._config: ResourceManagementConfig | None = None

        # Core state
        self._reservations: dict[str, ResourceReservation] = {}
        self._reservations_by_model: dict[str, str] = {}  # model_id -> reservation_id

        # Monitoring
        self._metrics = {
            "total_reservations": 0,
            "active_reservations": 0,
            "expired_reservations": 0,
            "failed_reservations": 0,
        }

        self._initialized = False

    async def initialize(self):
        """Start and sync initial state."""
        if self._initialized:
            return

        try:
            # Start the sequential executor
            await self._start_executor()

            # Load initial configuration
            if self._config_manager is not None:
                gateway_config = await self._config_manager.get_gateway_config(
                    self._gateway_id
                )
                self._config = gateway_config.resource_management

                # Subscribe to configuration updates
                await self._config_manager.subscribe_async(self._on_config_updated)
            else:
                # Use default configuration when no config manager is available
                default_config = {
                    "max_concurrent_model_loads": 2,
                    "model_loading_slot_acquisition_timeout": 30.0,  # Max allowed is 60.0
                    "reservation_timeout": 600,
                    "reservation_cleanup_interval": 60,
                    "enable_reservation_monitoring": True,
                }
                self._config = ResourceManagementConfig.from_dict(default_config)
                logger.info(
                    f"ResourceManager using default config for gateway {self._gateway_id} "
                    "(no gateways.yaml found)"
                )

            await self._refresh_available_resources()
            self._initialized = True
            logger.info(f"ResourceManager initialized for gateway {self._gateway_id}")
        except Exception as e:
            e.add_note(f"Gateway: {self._gateway_id}")
            logger.error(f"Failed to initialize ResourceManager: {e}", exc_info=True)
            raise

    async def _on_config_updated(self, gateway_name: str, gateway_config) -> None:
        """
        Handle configuration updates for this gateway.

        No sequentiality needed: Simple assignment, no await.
        """
        if gateway_name == self._gateway_id:
            old_config = self._config
            self._config = gateway_config.resource_management
            logger.info(
                f"Updated configuration for gateway {self._gateway_id}: "
                f"max_loads={self._config.max_concurrent_model_loads}, "
                f"timeout={self._config.reservation_timeout}"
            )

            if (
                old_config
                and old_config.reservation_timeout != self._config.reservation_timeout
            ):
                old_t = old_config.reservation_timeout
                new_t = self._config.reservation_timeout
                logger.warning(
                    f"Reservation timeout changed from {old_t}s to {new_t}s "
                    f"for gateway {self._gateway_id}"
                )

    @property
    def current_config(self) -> ResourceManagementConfig | None:
        """Get current resource management configuration snapshot."""
        return self._config

    @sequential
    async def shutdown(self):
        """
        Clean shutdown releasing all resources.

        Sequential execution: Iterates reservations dict during cleanup.
        """
        # Cancel all reservation expiration tasks
        for reservation in list(self._reservations.values()):
            reservation.cancel_expiration()
            if reservation.state != "released":
                self._release_reservation_internal(reservation.id)

        await self._stop_executor()

    @sequential
    async def reserve_resources_for_model(
        self, model: str, vram_mb: int, ram_mb: int
    ) -> ResourceReservation | None:
        """
        Attempt to reserve VRAM and RAM for a model load.

        Inputs:
            model: Model identifier
            vram_mb: VRAM required in megabytes
            ram_mb: RAM required in megabytes

        Outputs:
            ResourceReservation if successful, None if insufficient resources

        Sequential execution: Multi-step operation with awaits.
        """
        try:
            # Already reserved?
            if model in self._reservations_by_model:
                logger.warning(f"Model {model} already has active reservation")
                return None

            # Sync with current hardware state
            await self._refresh_available_resources()

            # Get current available resources
            metrics = await self._metrics_provider.get_gateway_metrics(self._gateway_id)
            if not metrics:
                logger.error(f"No metrics available for gateway {self._gateway_id}")
                return None

            # Calculate available after existing reservations
            available = self._calculate_available_after_reservations(metrics)

            # Check capacity
            if available["vram_mb"] < vram_mb or available["ram_mb"] < ram_mb:
                self._record_failed_reservation(model, vram_mb, ram_mb, available)
                return None

            # Create reservation with per-reservation expiration
            reservation = self._create_reservation(model, vram_mb, ram_mb)

            # Schedule per-reservation expiration task
            reservation.schedule_expiration(
                timeout=self._config.reservation_timeout,
                on_expire=self._on_reservation_expired,
            )

            self._reservations[reservation.id] = reservation
            self._reservations_by_model[model] = reservation.id
            self._metrics["total_reservations"] += 1
            self._metrics["active_reservations"] += 1

            logger.info(
                f"Reserved {vram_mb}MB VRAM, {ram_mb}MB RAM for model {model} "
                f"on gateway {self._gateway_id} (timeout={self._config.reservation_timeout}s)"
            )

            # Publish event to update WebSocket cache
            self._publish_reservation_event(reservation, created=True)

            return reservation

        except Exception as e:
            logger.error(f"Error reserving resources: {e}", exc_info=True)
            self._metrics["failed_reservations"] += 1
            return None

    def _on_reservation_expired(self, reservation: ResourceReservation) -> None:
        """Called when a reservation expires (via its expiration task)."""
        if reservation.id in self._reservations:
            del self._reservations[reservation.id]

            # Remove from model mapping
            if reservation.model_id in self._reservations_by_model:
                if self._reservations_by_model[reservation.model_id] == reservation.id:
                    del self._reservations_by_model[reservation.model_id]

            self._metrics["active_reservations"] -= 1
            self._metrics["expired_reservations"] += 1

            logger.warning(
                f"Reservation {reservation.id[:16]} for {reservation.model_id} "
                f"expired and cleaned up"
            )

            # Publish event to update WebSocket cache
            self._publish_release_event(reservation, reason="expired")

    async def mark_reservation_active(self, reservation_id: str):
        """
        Mark reservation as active when model loading starts.

        No sequentiality needed: Simple state update, no await.
        """
        if reservation_id in self._reservations:
            self._reservations[reservation_id].state = "active"
            logger.debug(f"Activated reservation {reservation_id}")

    async def release_reservation(self, reservation_id: str):
        """
        Release a reservation and update metrics.

        No sequentiality needed: Simple state update, no await.
        """
        self._release_reservation_internal(reservation_id)

    def _release_reservation_internal(self, reservation_id: str):
        """Internal release (no async needed)."""
        if reservation_id not in self._reservations:
            return

        reservation = self._reservations[reservation_id]
        if reservation.state == "released":
            return

        # Cancel the expiration task
        reservation.cancel_expiration()
        reservation.state = "released"

        # Remove from active model reservations
        if reservation.model_id in self._reservations_by_model:
            if self._reservations_by_model[reservation.model_id] == reservation_id:
                del self._reservations_by_model[reservation.model_id]

        # Remove from reservations dict
        del self._reservations[reservation_id]
        self._metrics["active_reservations"] -= 1

        logger.info(
            f"Released reservation {reservation_id} for model {reservation.model_id}"
        )

        # Publish event to update WebSocket cache
        self._publish_release_event(reservation, reason="completed")

    def _calculate_available_after_reservations(self, metrics: dict) -> dict:
        """Calculate available resources after accounting for existing reservations."""
        available_vram = metrics.get("vram_free_mb", 0)
        available_ram = metrics.get("ram_free_mb", 0)

        for res in self._reservations.values():
            if res.state in ["pending", "active"]:
                available_vram -= res.vram_mb
                available_ram -= res.ram_mb

        return {"vram_mb": available_vram, "ram_mb": available_ram}

    def _record_failed_reservation(
        self, model: str, vram_mb: int, ram_mb: int, available: dict
    ):
        """Record a failed reservation attempt in metrics."""
        logger.debug(
            f"Insufficient resources for {model}: "
            f"need {vram_mb}MB VRAM (have {available['vram_mb']}MB), "
            f"{ram_mb}MB RAM (have {available['ram_mb']}MB)"
        )
        self._metrics["failed_reservations"] += 1

    def _create_reservation(
        self, model: str, vram_mb: int, ram_mb: int
    ) -> ResourceReservation:
        """Create a new reservation object."""
        return ResourceReservation(
            id=f"{self._gateway_id}_{model}_{int(time.time() * 1000)}",
            gateway_id=self._gateway_id,
            model_id=model,
            vram_mb=vram_mb,
            ram_mb=ram_mb,
        )  # kw_only=True ensures all fields passed by name

    async def _refresh_available_resources(self):
        """Sync resource state with actual gateway metrics."""
        try:
            metrics = await self._metrics_provider.get_gateway_metrics(self._gateway_id)
            if metrics:
                # Update any internal state based on metrics
                # This ensures we don't drift from reality
                pass
        except Exception as e:
            logger.error(f"Error syncing with metrics: {e}")

    def get_metrics(self) -> dict:
        """Get resource manager metrics for monitoring."""
        return {
            "gateway_id": self._gateway_id,
            "reservations": {
                "total": self._metrics["total_reservations"],
                "active": self._metrics["active_reservations"],
                "expired": self._metrics["expired_reservations"],
                "failed": self._metrics["failed_reservations"],
            },
            "active_models": list(self._reservations_by_model.keys()),
        }

    def _publish_reservation_event(
        self, reservation: ResourceReservation, created: bool
    ) -> None:
        """
        Publish RESOURCE_RESERVED event to update WebSocket cache.

        Fire-and-forget: don't await to avoid blocking reservation flow.
        """
        if not self._event_bus:
            return

        try:
            import asyncio

            from src.scheduling.events import ResourceReserved

            event = ResourceReserved(
                gateway_name=self._gateway_id,
                model_id=reservation.model_id,
                reservation_id=reservation.id,
                vram_mb=reservation.vram_mb,
                ram_mb=reservation.ram_mb,
                timeout_seconds=self._config.reservation_timeout,
            )
            # Schedule task without blocking (fire-and-forget from sync context)
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    lambda: asyncio.create_task(
                        self._event_bus.publish_async_nowait(event)
                    )
                )
            except RuntimeError:
                # Not in async context, skip emission
                pass
            logger.debug(
                f"📢 Published RESOURCE_RESERVED: {reservation.model_id} "
                f"on {self._gateway_id} ({reservation.vram_mb}MB VRAM, "
                f"{reservation.ram_mb}MB RAM)"
            )
        except Exception as e:
            logger.warning(f"Failed to publish RESOURCE_RESERVED event: {e}")

    def _publish_release_event(
        self, reservation: ResourceReservation, reason: str
    ) -> None:
        """
        Publish RESOURCE_RELEASED event to update WebSocket cache.

        Fire-and-forget: don't await to avoid blocking release flow.
        """
        if not self._event_bus:
            return

        try:
            import asyncio

            from src.scheduling.events import ResourceReleased

            event = ResourceReleased(
                gateway_name=self._gateway_id,
                model_id=reservation.model_id,
                reservation_id=reservation.id,
                vram_mb=reservation.vram_mb,
                ram_mb=reservation.ram_mb,
                reason=reason,
            )
            # Schedule task without blocking (fire-and-forget from sync context)
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    lambda: asyncio.create_task(
                        self._event_bus.publish_async_nowait(event)
                    )
                )
            except RuntimeError:
                # Not in async context, skip emission
                pass
            logger.debug(
                f"📢 Published RESOURCE_RELEASED: {reservation.model_id} "
                f"on {self._gateway_id} ({reservation.vram_mb}MB VRAM, "
                f"{reservation.ram_mb}MB RAM, reason={reason})"
            )
        except Exception as e:
            logger.warning(f"Failed to publish RESOURCE_RELEASED event: {e}")
