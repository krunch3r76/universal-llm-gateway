"""
Load orchestration operations for model loading.

Contains the main ensure_on_gateway method and related coordination logic.
Extracted from loading.py to maintain SLOC limits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from ..types import (
    ConfigHelper,
    MissingResourceRequirementsError,
    ResourceRequirementsProvider,
    SchedulerConfigProvider,
)
from .coordination import GlobalModelLoadCoordinator, ModelLoadCoordinator
from .load_execution import execute_load_request, reserve_resources
from .status import (
    ModelLoadingStatus,
    get_model_status,
)
from .waiting import LoadResult, ModelLoadWaiter

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ...gateway_error_interceptor import GatewayErrorInterceptor
    from ...resource_manager import GatewayResourceManager

logger = get_logger(__name__)


class ModelLoadOrchestrator:
    """
    Handles complex model loading orchestration logic.

    Extracted from ModelLoadingOperations to maintain SLOC limits.
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

        self._loading_models: dict[str, set[str]] = {}

    async def _wait_for_load_with_fallback(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        gateway_name: str,
    ) -> ModelLoadingStatus:
        """
        Wait for load with timeout fallback to HTTP status check.

        Uses event-driven waiting via load waiter, falls back to HTTP status on timeout.
        """
        if not self._load_waiter:
            logger.error(f"No load waiter configured for {model_id} - cannot wait")
            return ModelLoadingStatus.FAILED

        result = await self._load_waiter.wait_for_load(
            gateway_name, model_id, self._config.model_loading_timeout
        )
        match result:
            case LoadResult.LOADED:
                return ModelLoadingStatus.LOADED
            case LoadResult.FAILED:
                return ModelLoadingStatus.FAILED
            case LoadResult.TIMEOUT:
                # Timeout: do final status check
                status = await get_model_status(gateway, model_id)
                return (
                    ModelLoadingStatus.LOADED
                    if status.is_loaded
                    else ModelLoadingStatus.TIMED_OUT
                )
            case _:
                return ModelLoadingStatus.FAILED

    async def _check_coordinator_state(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        gateway_name: str,
    ) -> ModelLoadingStatus | None:
        """
        Check coordinator state for sticky routing.

        Returns ModelLoadingStatus if state is conclusive, None to continue checking.
        """
        # Check if coordinator says model is loaded
        loaded_on = self._global_load_coordinator.where_is_loaded(model_id)
        if loaded_on == gateway_name:
            if self._global_load_coordinator.was_load_coordinated(model_id):
                # We coordinated this load - trust it completely
                logger.info(
                    "✅ Model %s confirmed on %s (coordinator-verified, skipping HTTP"
                    "check)",
                    model_id,
                    gateway_name,
                )
                return ModelLoadingStatus.LOADED
            # External load - will verify via HTTP later
            logger.debug(
                "Model %s on %s - coordinator NOT verified (external load or stale),"
                "will check HTTP",
                model_id,
                gateway_name,
            )

        # Check for ERROR state
        error_state = self._global_load_coordinator.get_error_state(model_id)
        if error_state:
            error_gateway, error_msg = error_state
            if error_gateway == gateway_name:
                logger.info(
                    f"Model {model_id} in ERROR state on {gateway_name}"
                    f"(coordinator): {error_msg}. Will clear and retry"
                )

        # Check for LOADING state
        loading_on = self._global_load_coordinator.where_is_loading(model_id)
        if loading_on == gateway_name:
            logger.debug(f"Model {model_id} loading on {gateway_name} (coordinator)")
            return await self._wait_for_load_with_fallback(
                gateway, model_id, gateway_name
            )

        return None

    async def _check_websocket_cache(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        gateway_name: str,
    ) -> ModelLoadingStatus | None:
        """
        Check WebSocket cache for non-sticky routing.

        Returns ModelLoadingStatus if state is conclusive, None to continue checking.
        """

        def _model_in_set(model_id: ModelId, model_set: frozenset[str]) -> bool:
            """
            Check if model_id matches any model in model_set using normalized
                comparison.

            Gateway returns canonical IDs (without -hybrid), so we normalize for
                matching.
            """
            try:
                normalized_query = model_id.normalized
                return any(
                    ModelId.parse(m).normalized == normalized_query for m in model_set
                )
            except ValueError:
                # Fallback to exact match if parsing fails
                return str(model_id) in model_set

        # Use WebSocket cached state (event-driven, instant)
        loaded_ws = gateway.client.get_loaded_models()
        busy_ws = gateway.client.get_busy_models()
        loading_ws = gateway.client.get_loading_models()

        # Check using normalized matching
        is_loaded = _model_in_set(model_id, loaded_ws)
        is_busy = _model_in_set(model_id, busy_ws)
        is_loading = _model_in_set(model_id, loading_ws)

        if is_loaded or is_busy:
            logger.debug(
                "✅ Model %s confirmed loaded on %s (WebSocket cache, no HTTP)",
                model_id,
                gateway_name,
            )
            return ModelLoadingStatus.LOADED

        if is_loading:
            logger.debug(
                f"Model {model_id} loading on {gateway_name} (WebSocket cache)"
            )
            return await self._wait_for_load_with_fallback(
                gateway, model_id, gateway_name
            )

        # Not found in WebSocket cache
        logger.debug(
            f"Model {model_id} not in WebSocket cache on"
            f"{gateway_name}, will attempt load"
        )
        return None

    async def _check_http_status(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        gateway_name: str,
        sticky: bool,
    ) -> ModelLoadingStatus | None:
        """
        Check HTTP status for model on gateway.

        Returns ModelLoadingStatus if state is conclusive, None to continue to loading.
        """
        gw_status = await get_model_status(gateway, model_id)

        if not gw_status.reachable:
            logger.warning(f"Gateway {gateway.config.name} unreachable")
            return ModelLoadingStatus.FAILED

        if gw_status.is_loaded:
            # Exact variant is loaded on gateway
            if sticky:
                self._global_load_coordinator.on_model_loaded_event(
                    model_id, gateway_name
                )
            logger.debug(
                "✅ Model %s confirmed loaded on %s (HTTP check), updated coordinator",
                model_id,
                gateway_name,
            )
            return ModelLoadingStatus.LOADED

        if gw_status.is_busy:
            # Model is loaded and serving requests
            if sticky:
                self._global_load_coordinator.on_model_loaded_event(
                    model_id, gateway_name
                )
            logger.debug(
                f"Model {model_id} is loaded and busy on"
                f"{gateway.config.name}, proceeding with request"
            )
            return ModelLoadingStatus.LOADED

        if gw_status.is_error:
            logger.info(
                f"Model {model_id} in ERROR state on {gateway.config.name}"
                f"(HTTP fallback), will clear and retry"
            )

        if gw_status.is_loading:
            return await self._wait_for_load_with_fallback(
                gateway, model_id, gateway_name
            )

        # Model not loaded - check for routing key conflict
        coordinator_thinks_loaded = (
            sticky
            and self._global_load_coordinator.where_is_loaded(model_id) == gateway_name
        )
        if coordinator_thinks_loaded:
            logger.info(
                "🔄 Coordinator routing key conflict: %s not loaded, "
                "but routing key marked as loaded on %s. Will attempt eviction and"
                "load.",
                model_id,
                gateway_name,
            )

        return None

    async def ensure_on_gateway(
        self,
        gateway: GatewayInstance,
        model_id: str | ModelId,
        resource_manager: GatewayResourceManager,
        *,
        sticky: bool = True,
    ) -> ModelLoadingStatus:
        """
        Ensure model is loaded on the specified gateway.

        Checks coordinator state, WebSocket cache, and HTTP status before
        attempting to load. Respects sticky routing for model coordination.

        Args:
            gateway: Target gateway instance
            model_id: Model to ensure is loaded (ModelId or str for backward compat)
            resource_manager: Resource manager for the gateway
            sticky: If True, use global coordination; if False, allow multi-gateway

        Returns:
            ModelLoadingStatus indicating success or failure
        """
        # Parse at boundary if string
        parsed_model_id = (
            ModelId.parse(model_id) if isinstance(model_id, str) else model_id
        )
        gateway_name = gateway.config.name

        # Check coordinator state (sticky mode only)
        if sticky:
            if result := await self._check_coordinator_state(
                gateway, parsed_model_id, gateway_name
            ):
                return result

        # Check WebSocket cache (non-sticky mode)
        if not sticky:
            if result := await self._check_websocket_cache(
                gateway, parsed_model_id, gateway_name
            ):
                return result

        # Check HTTP status (fallback for both modes)
        if result := await self._check_http_status(
            gateway, parsed_model_id, gateway_name, sticky
        ):
            return result

        # Model not loaded - check if already loading
        if self._is_already_loading(gateway_name, parsed_model_id):
            return await self._wait_for_existing_load(gateway_name, parsed_model_id)

        # Proceed with loading
        logger.debug(f"Loading model {parsed_model_id} on {gateway.config.name}")
        if sticky:
            return await self._load_with_coordination(
                gateway, parsed_model_id, resource_manager
            )
        return await self._load_without_global_coordination(
            gateway, parsed_model_id, resource_manager
        )

    async def _load_without_global_coordination(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        resource_manager: GatewayResourceManager,
    ) -> ModelLoadingStatus:
        """Load model without global coordination (non-sticky routing)."""
        gateway_name = gateway.config.name

        # Reserve resources
        try:
            requirements = await self._get_requirements(model_id)
        except MissingResourceRequirementsError as e:
            logger.error(f"Cannot load {model_id}: {e}")
            return ModelLoadingStatus.FAILED

        reservation = await reserve_resources(
            resource_manager, model_id, gateway_name, requirements
        )
        if not reservation:
            logger.warning(f"Resource reservation failed for {model_id}")
            return ModelLoadingStatus.FAILED

        # Execute load
        return await execute_load_request(
            gateway,
            model_id,
            self._gateway_interceptor,
            resource_manager,
            reservation,
            self._config.model_loading_timeout,
            self._load_waiter,
        )

    def _is_already_loading(self, gateway_name: str, model_id: ModelId) -> bool:
        key = model_id.routing_key
        return (
            gateway_name in self._loading_models
            and key in self._loading_models[gateway_name]
        )

    async def _wait_for_existing_load(
        self, gateway_name: str, model_id: ModelId
    ) -> ModelLoadingStatus:
        """Wait for an existing load operation to complete."""
        logger.debug(f"Model {model_id} already loading on {gateway_name}, waiting...")

        # Wait for the loading set to be updated
        while self._is_already_loading(gateway_name, model_id):
            await asyncio.sleep(0.1)

        # Check final state from coordinator
        if self._global_load_coordinator.where_is_loaded(model_id) == gateway_name:
            return ModelLoadingStatus.LOADED

        error_state = self._global_load_coordinator.get_error_state(model_id)
        if error_state and error_state[0] == gateway_name:
            return ModelLoadingStatus.FAILED

        return ModelLoadingStatus.FAILED

    async def _load_with_coordination(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        resource_manager: GatewayResourceManager,
    ) -> ModelLoadingStatus:
        """Load model with global coordination (sticky routing)."""
        gateway_name = gateway.config.name

        # Mark as loading (use routing_key for dict key)
        if gateway_name not in self._loading_models:
            self._loading_models[gateway_name] = set()
        self._loading_models[gateway_name].add(model_id.routing_key)

        try:
            # Coordinate load
            result = await self._global_load_coordinator.request_model_load(
                model_id, gateway_name
            )

            # DIAGNOSTIC: Log coordinator decision
            logger.info(
                f"📋 Coordinator response for {model_id}:"
                f"should_load={result.should_load}, f"
                f"redirect={result.redirect_gateway}, error={result.error_message}"
            )

            if result.should_load:
                # Actually execute the load (reserve resources + HTTP request to
                # Gateway)
                status = await self._execute_coordinated_load(
                    gateway, model_id, resource_manager
                )

                # Report result to coordinator
                await self._global_load_coordinator.report_load_complete(
                    model_id,
                    gateway_name,
                    succeeded=(status == ModelLoadingStatus.LOADED),
                )

                return status
            elif result.redirect_gateway:
                # Model already loaded elsewhere - NOT a failure for sticky models
                logger.info(
                    f"Model {model_id} already loaded on {result.redirect_gateway} "
                    f"(requested: {gateway_name})"
                )
                return ModelLoadingStatus.LOADED
            elif result.wait_event:
                # Model currently loading - wait for it
                logger.debug(
                    f"Model {model_id} loading on {result.redirect_gateway}, waiting..."
                )
                try:
                    await asyncio.wait_for(
                        result.wait_event.wait(),
                        timeout=self._config.model_loading_timeout,
                    )
                    return ModelLoadingStatus.LOADED
                except TimeoutError:
                    logger.error(
                        f"Timeout waiting for {model_id} to load on"
                        f"{result.redirect_gateway}"
                    )
                    return ModelLoadingStatus.FAILED
            else:
                # Coordination denied without redirect or wait - true failure
                logger.error(
                    f"Load coordination denied without redirect: {result.error_message}"
                )
                return ModelLoadingStatus.FAILED

        finally:
            # Cleanup loading state
            await self._cleanup_loading_state(gateway_name, model_id)

    async def _wait_for_per_gateway_load(
        self, gateway_name: str, model_id: ModelId
    ) -> ModelLoadingStatus:
        """Wait for per-gateway load to complete."""
        # Wait for coordinator to report success or failure
        timeout = self._config.model_loading_timeout

        for _ in range(int(timeout * 10)):  # Check every 100ms
            if self._global_load_coordinator.where_is_loaded(model_id) == gateway_name:
                return ModelLoadingStatus.LOADED

            error_state = self._global_load_coordinator.get_error_state(model_id)
            if error_state and error_state[0] == gateway_name:
                return ModelLoadingStatus.FAILED

            await asyncio.sleep(0.1)

        return ModelLoadingStatus.TIMED_OUT

    async def _execute_coordinated_load(
        self,
        gateway: GatewayInstance,
        model_id: ModelId,
        resource_manager: GatewayResourceManager,
    ) -> ModelLoadingStatus:
        """Execute the actual load request with resource management."""
        gateway_name = gateway.config.name

        # Reserve resources
        try:
            requirements = await self._get_requirements(model_id)
        except MissingResourceRequirementsError as e:
            logger.error(f"Cannot load {model_id}: {e}")
            return ModelLoadingStatus.FAILED

        reservation = await reserve_resources(
            resource_manager, model_id, gateway_name, requirements
        )
        if not reservation:
            logger.warning(f"Resource reservation failed for {model_id}")
            return ModelLoadingStatus.FAILED

        # Execute load
        return await execute_load_request(
            gateway,
            model_id,
            self._gateway_interceptor,
            resource_manager,
            reservation,
            self._config.model_loading_timeout,
            self._load_waiter,
        )

    async def _cleanup_loading_state(
        self, gateway_name: str, model_id: ModelId, delay: float = 5.0
    ) -> None:
        """Clean up loading state after delay."""
        await asyncio.sleep(delay)

        key = model_id.routing_key
        if (
            gateway_name in self._loading_models
            and key in self._loading_models[gateway_name]
        ):
            self._loading_models[gateway_name].discard(key)
            if not self._loading_models[gateway_name]:
                del self._loading_models[gateway_name]
