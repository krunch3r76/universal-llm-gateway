"""
Resource-aware model manager for multi-gateway orchestration.

Thin orchestrator that composes modular loading and routing components.
"""

from pathlib import Path

from model_id import ModelId
from universal_logging import get_logger

from gateways import GatewayInstance, SingleGatewayManager

from ..resource_management import (
    GatewayConfigManager,
    ResourceManagementConfigError,
)
from ..stargate_config import StargateConfig
from .common import ErrorNormalizer, GatewayErrorInterceptor, GatewayResourceManager
from .control_plane import (
    GatewayStatusResult,
    GlobalModelLoadCoordinator,
    MissingResourceRequirementsError,
    ModelLoadCoordinator,
    ModelLoadingStatus,
    ModelStatus,
    ResourceRequirements,
)
from .control_plane.model_lifecycle.loading import ModelLoadingOperations
from .control_plane.model_lifecycle.waiting import ModelLoadWaiter
from .control_plane.placement.single.operations import ModelRoutingOperations

__all__ = [
    "GatewayMetricsProvider",
    "GatewayStatusResult",
    "ModelLoadingStatus",
    "ModelStatus",
    "ResourceAwareModelManager",
]

logger = get_logger(__name__)


class GatewayMetricsProvider:
    """
    Async metrics provider adapting SingleGatewayManager to GatewayResourceManager.
    """

    def __init__(self, gateway_manager: SingleGatewayManager, gateway_name: str):
        self.gateway_manager = gateway_manager
        self.gateway_name = gateway_name
        self._gateway_cache: GatewayInstance | None = None

    async def get_gateway_metrics(self, gateway_id: str) -> dict | None:
        """Get current resource metrics for a gateway."""
        try:
            # SingleGatewayManager has a single gateway attribute, not gateways dict
            if not self._gateway_cache or self._gateway_cache.config.name != gateway_id:
                gateway = self.gateway_manager.gateway
                if gateway and gateway.config.name == gateway_id:
                    self._gateway_cache = gateway

            gateway = self._gateway_cache
            if not gateway or not gateway.client.is_connected():
                return None

            # WebSocket-only: real-time cached status (no HTTP fallback)
            status = gateway.client.get_resource_status()
            if not status:
                # Gateway not connected = not available for reservation
                return None

            return {
                "vram_free_mb": status.available_vram_mb,
                "ram_free_mb": status.available_ram_mb,
                "loaded_models": list(status.loaded_models),
                "busy_models": list(status.busy_models),
            }
        except Exception as e:
            logger.error(f"Error getting metrics for gateway {gateway_id}: {e}")
            return None


class ResourceAwareModelManager:
    """
    Lightweight manager keeping routing decisions aligned with gateway health.

    Orchestrates modular components for loading and routing operations.
    Uses atomic VRAM reservation to prevent concurrent model loading race conditions.
    Uses event-driven model load waiting (WebSocket callbacks) instead of polling.
    """

    def __init__(
        self,
        gateway_manager: SingleGatewayManager,
        config: StargateConfig,
        event_bus=None,
    ):
        self.gateway_manager = gateway_manager
        self.config = config
        self.event_bus = event_bus

        self._resource_managers: dict[str, GatewayResourceManager] = {}
        self._config_manager: GatewayConfigManager | None = None
        self._resource_management_enabled = False

        self._load_coordinator = ModelLoadCoordinator()
        self._global_load_coordinator = GlobalModelLoadCoordinator()

        self._gateway_interceptor = GatewayErrorInterceptor(ErrorNormalizer)

        # Event-driven load waiter (replaces polling)
        self._load_waiter = ModelLoadWaiter(
            self._global_load_coordinator, event_bus=event_bus
        )

        self._loading_ops = ModelLoadingOperations(
            load_coordinator=self._load_coordinator,
            global_load_coordinator=self._global_load_coordinator,
            gateway_interceptor=self._gateway_interceptor,
            config_getter=self._get_scheduler_config,
            requirements_provider=self._get_resource_requirements,
            load_waiter=self._load_waiter,
        )

        self._routing_ops = ModelRoutingOperations(
            gateway_manager=gateway_manager,
            loading_ops=self._loading_ops,
            global_load_coordinator=self._global_load_coordinator,
            resource_manager_getter=self.get_resource_manager,
            config_getter=self._get_scheduler_config,
        )

    def _get_scheduler_config(self) -> dict:
        if hasattr(self.config, "get_scheduler_config"):
            return self.config.get_scheduler_config()
        return {}

    async def _get_resource_requirements(
        self, model_id: ModelId
    ) -> ResourceRequirements:
        """Get resource requirements from model configuration.

        Args:
            model_id: ModelId object (parsed at request boundary)

        Raises:
            MissingResourceRequirementsError: If configuration unavailable or incomplete
        """
        # Pass ModelId object directly - method handles appropriate lookup internally
        model_config = await self.gateway_manager.fetch_model_configuration(model_id)

        if not model_config:
            raise MissingResourceRequirementsError(str(model_id))

        vram_mb = model_config.vram_usage
        ram_mb = model_config.ram_usage

        if vram_mb is None or ram_mb is None:
            raise MissingResourceRequirementsError(str(model_id))

        return ResourceRequirements(vram_mb=vram_mb, ram_mb=ram_mb)

    async def initialize(self) -> None:
        """Initialize resource management system with gateway configurations."""
        try:
            # CRITICAL: Always start the coordinator's executor first, even if
            # there's no gateways.yaml. The coordinator is used for model sync
            # callbacks when gateways connect, regardless of VRAM management.
            self._global_load_coordinator.clear_all_state()
            await self._global_load_coordinator.start()
            logger.debug("✅ Global load coordinator executor started")

            config_path = Path("config/gateways.yaml")
            if config_path.exists():
                # Load explicit gateway configurations from YAML
                self._config_manager = GatewayConfigManager(config_path)
                await self._config_manager.initialize()

                gateway_configs = await self._config_manager.get_all_gateway_configs()
                for gateway_name, gateway_config in gateway_configs.items():
                    await self._register_gateway_resource_management(
                        gateway_name, gateway_config
                    )

                self._resource_management_enabled = len(self._resource_managers) > 0

                if self._resource_management_enabled:
                    logger.info(
                        f"✅ VRAM reservation enabled for "
                        f"{len(self._resource_managers)} gateway(s)"
                    )

                # Register gateways with load waiter for event-driven load waiting
                self._register_load_waiter_callbacks()
            else:
                # No gateways.yaml - default resource managers will be registered
                # after gateway connection
                # (see register_default_resource_managers_after_connection)
                logger.warning(f"Gateway config not found: {config_path}")
                logger.info(
                    "Will register default resource managers after gateway connection"
                )

        except ResourceManagementConfigError as e:
            logger.error(f"Failed to initialize resource management: {e}")
        except Exception as e:
            logger.error(f"Unexpected error initializing: {e}", exc_info=True)

    def _register_load_waiter_callbacks(self) -> None:
        """Register gateway with the load waiter for event-driven notifications."""
        gateway = self.gateway_manager.gateway
        if gateway is not None:
            try:
                self._load_waiter.register_gateway(gateway)
                logger.info(
                    f"✅ Event-driven load waiting enabled for {gateway.config.name}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to register load waiter for {gateway.config.name}: {e}"
                )

    async def register_default_resource_managers_after_connection(self) -> None:
        """Register default resource manager for the connected gateway.

        Called after gateway connection when gateways.yaml doesn't exist.
        Creates a resource manager with default configuration.
        """
        try:
            gateway = self.gateway_manager.gateway
            if gateway is None:
                logger.warning("No gateway available to register resource manager")
                return

            gateway_name = gateway.config.name

            metrics_provider = GatewayMetricsProvider(
                self.gateway_manager, gateway_name
            )

            # Create resource manager without config_manager (uses defaults)
            resource_manager = GatewayResourceManager(
                gateway_id=gateway_name,
                metrics_provider=metrics_provider,
                state_manager=None,
                config_manager=None,  # None = use default config
                event_bus=self.gateway_manager.event_bus,
            )
            await resource_manager.initialize()
            self._resource_managers[gateway_name] = resource_manager

            logger.info(f"✅ Registered default resource manager for {gateway_name}")

            # Also register gateway with load waiter
            self._register_load_waiter_callbacks()
        except Exception as e:
            logger.error(
                f"Failed to register default resource managers: {e}", exc_info=True
            )

    async def _register_gateway_resource_management(
        self, gateway_name: str, gateway_config
    ) -> None:
        try:
            metrics_provider = GatewayMetricsProvider(
                self.gateway_manager, gateway_name
            )

            resource_manager = GatewayResourceManager(
                gateway_id=gateway_name,
                metrics_provider=metrics_provider,
                state_manager=None,
                config_manager=self._config_manager,
                event_bus=self.gateway_manager.event_bus,
            )
            await resource_manager.initialize()
            self._resource_managers[gateway_name] = resource_manager

            logger.info(f"✅ Registered resource management for {gateway_name}")

        except Exception as e:
            logger.error(f"Failed to register {gateway_name}: {e}", exc_info=True)

    async def shutdown(self) -> None:
        """Clean shutdown of all resource management components."""
        # Stop load waiter first (wakes up any pending waiters)
        self._load_waiter.stop()

        # Stop the global coordinator
        await self._global_load_coordinator.stop()

        # Stop the config manager
        if self._config_manager:
            await self._config_manager.shutdown()

        for gateway_name, resource_manager in self._resource_managers.items():
            try:
                await resource_manager.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down {gateway_name}: {e}")

        self._resource_managers.clear()

    def get_resource_manager(self, gateway_name: str) -> GatewayResourceManager | None:
        """Get resource manager for a specific gateway."""
        return self._resource_managers.get(gateway_name)

    async def ensure_model_loaded(
        self, model_id: ModelId, *, sticky: bool = True
    ) -> GatewayInstance:
        """
        Pick a healthy gateway and ensure model is loaded.

        Args:
            model_id: The model to load (ModelId object)

        Returns:
            GatewayInstance ready for inference

        Raises:
            HTTPException: 503 (no gateway), 504 (timeout), 500 (missing metadata)
        """
        return await self._routing_ops.ensure_model_loaded(model_id, sticky=sticky)

    async def get_model_status(
        self, gateway: GatewayInstance, model_id: ModelId
    ) -> GatewayStatusResult:
        """Get model status from gateway with reachability information."""
        return await self._loading_ops.get_model_status(gateway, model_id)

    def set_federated_load_orchestrator(self, orchestrator) -> None:
        """
        Inject federated load orchestrator for remote model loading.

        Args:
            orchestrator: FederatedLoadOrchestrator instance
        """
        self._routing_ops.set_federated_load_orchestrator(orchestrator)
