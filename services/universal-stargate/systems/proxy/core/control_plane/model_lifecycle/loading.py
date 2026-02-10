"""
Model loading operations for gateway orchestration.

Contains loading coordination logic. Execution helpers in load_execution.
Complex orchestration logic extracted to load_orchestration.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from model_id import ModelId
from universal_logging import get_logger

from ..types import (
    ConfigHelper,
    ResourceRequirementsProvider,
    SchedulerConfigProvider,
)
from .coordination import GlobalModelLoadCoordinator, ModelLoadCoordinator
from .load_execution import wait_for_unload
from .load_orchestration import ModelLoadOrchestrator
from .status import (
    GatewayStatusResult,
    ModelLoadingStatus,
    get_model_status,
)
from .waiting import LoadResult, ModelLoadWaiter

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ..gateway_error_interceptor import GatewayErrorInterceptor
    from ..resource_manager import GatewayResourceManager

logger = get_logger(__name__)


class ModelLoadingOperations:
    """
    Encapsulates model loading, unloading, and status operations.

    Composed into ResourceAwareModelManager.
    Complex orchestration logic delegated to ModelLoadOrchestrator.
    """

    def __init__(
        self,
        load_coordinator: ModelLoadCoordinator,
        global_load_coordinator: GlobalModelLoadCoordinator,
        gateway_interceptor: GatewayErrorInterceptor,
        config_getter: SchedulerConfigProvider,
        requirements_provider: ResourceRequirementsProvider,
        load_waiter: ModelLoadWaiter | None = None,
    ):
        self._load_coordinator = load_coordinator
        self._global_load_coordinator = global_load_coordinator
        self._gateway_interceptor = gateway_interceptor
        self._config = ConfigHelper(config_getter)
        self._get_requirements = requirements_provider
        self._load_waiter = load_waiter

        # Delegate complex orchestration to separate class
        self._orchestrator = ModelLoadOrchestrator(
            load_coordinator,
            global_load_coordinator,
            gateway_interceptor,
            config_getter,
            requirements_provider,
            load_waiter,
        )

    async def get_model_status(
        self, gateway: GatewayInstance, model_id: ModelId
    ) -> GatewayStatusResult:
        """Get model status from gateway with reachability information."""
        # Serialize for HTTP call to gateway
        return await get_model_status(gateway, str(model_id))

    async def wait_for_model_load_complete(
        self, gateway: GatewayInstance, model_id: ModelId
    ) -> ModelLoadingStatus:
        """
        Wait for a LOADING model to complete (event-driven).

        Uses WebSocket callbacks for instant notification via asyncio.Event.
        No polling - fully event-driven with timeout fallback to status check.
        """
        gateway_name = gateway.config.name
        timeout = self._config.model_loading_timeout

        if not self._load_waiter:
            logger.error(f"No load waiter configured for {model_id} - cannot wait")
            return ModelLoadingStatus.FAILED

        # Serialize for load_waiter (uses string keys internally)
        result = await self._load_waiter.wait_for_load(
            gateway_name, str(model_id), timeout
        )

        match result:
            case LoadResult.LOADED:
                return ModelLoadingStatus.LOADED
            case LoadResult.FAILED:
                return ModelLoadingStatus.FAILED
            case LoadResult.TIMEOUT:
                # Timeout: do final status check
                status = await get_model_status(gateway, model_id)
                if status.is_loaded:
                    return ModelLoadingStatus.LOADED
                return ModelLoadingStatus.TIMED_OUT
            case _:
                return ModelLoadingStatus.FAILED

    async def unload_model(
        self, gateway: GatewayInstance, model_id: ModelId, timeout: float | None = None
    ) -> None:
        """
        Unload a model from the gateway to ensure clean state.

        Args:
            gateway: Gateway instance to unload from
            model_id: Model to unload
            timeout: Optional timeout override (default: config.max_unload_wait).
                     Use shorter timeout (e.g., 5s) for ERROR states where
                     process is likely dead.
        """
        try:
            client = gateway.client.get_http_client()
            response = await self._gateway_interceptor.safe_gateway_call(
                client=client,
                method="DELETE",
                url=f"/api/v1/models/{model_id}",  # __str__ auto-converts
                gateway_name=gateway.config.name,
                operation="model_unload",
            )

            if response.status_code == 200:
                logger.info(
                    f"✅ Unload request accepted for {model_id} on "
                    f"{gateway.config.name}"
                )
            else:
                logger.warning(
                    f"Model {model_id} unload returned {response.status_code}"
                )

            # Event-driven wait for unload completion
            # Use provided timeout or default from config
            unload_timeout = timeout or self._config.max_unload_wait

            if self._load_waiter:
                # Serialize for load_waiter
                result = await self._load_waiter.wait_for_unload(
                    gateway.config.name, str(model_id), unload_timeout
                )
                if result == LoadResult.TIMEOUT:
                    logger.warning(
                        f"Unload timeout for {model_id} on {gateway.config.name} "
                        f"after {unload_timeout}s"
                    )
            else:
                # Fallback: wait for unload without event-driven waiter
                # Serialize for wait_for_unload
                await wait_for_unload(
                    gateway, str(model_id), self._gateway_interceptor, unload_timeout
                )

        except HTTPException:
            # Gateway error (404, 500, etc.) - model might already be unloaded
            logger.info(f"Gateway error unloading {model_id}, likely already unloaded")
        except Exception as e:
            logger.warning(
                f"Unexpected error unloading {model_id}: {e}. Proceeding anyway."
            )

    async def ensure_on_gateway(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        resource_manager: GatewayResourceManager,
        *,
        sticky: bool = True,
    ) -> ModelLoadingStatus:
        """Ensure model is loaded on the specified gateway."""
        return await self._orchestrator.ensure_on_gateway(
            gateway, model_id, resource_manager, sticky=sticky
        )
