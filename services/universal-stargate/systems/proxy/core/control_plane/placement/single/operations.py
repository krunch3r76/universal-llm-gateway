"""
Model routing operations for gateway orchestration.

Contains immediate routing logic. Queue-based routing delegated to queue_wait.
"""

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from ...model_lifecycle.catalog_existence import check_model_exists_anywhere
from ...model_lifecycle.waiting.queue_wait import await_queue_with_retry
from ...types import ConfigHelper, ResourceManagerProvider, SchedulerConfigProvider

if TYPE_CHECKING:
    from gateways import GatewayInstance, SingleGatewayManager
    from systems.federation.master.orchestration import FederatedLoadOrchestrator
    from systems.routing.selection.types import Gateway

    from ...model_lifecycle.coordination import GlobalModelLoadCoordinator
    from ...model_lifecycle.loading import ModelLoadingOperations

logger = get_logger(__name__)


class ModelRoutingOperations:
    """
    Encapsulates model routing and queue operations.

    Composed into ResourceAwareModelManager for clean separation.
    """

    def __init__(
        self,
        gateway_manager: "SingleGatewayManager",
        loading_ops: "ModelLoadingOperations",
        global_load_coordinator: "GlobalModelLoadCoordinator",
        resource_manager_getter: ResourceManagerProvider,
        config_getter: SchedulerConfigProvider,
    ):
        self._gateway_manager = gateway_manager
        self._loading_ops = loading_ops
        self._global_load_coordinator = global_load_coordinator
        self._get_resource_manager = resource_manager_getter
        self._config = ConfigHelper(config_getter)

        # Federation orchestrator (set via DI, Master mode only)
        self._federated_load_orchestrator: FederatedLoadOrchestrator | None = None

    def set_federated_load_orchestrator(self, orchestrator) -> None:
        """Inject federated load orchestrator for remote model loading."""
        self._federated_load_orchestrator = orchestrator

    async def ensure_model_loaded(
        self, model_id: ModelId, request=None, *, sticky: bool = True
    ) -> "GatewayInstance | Gateway":
        """
        Pick a healthy gateway and ensure model is loaded.

        For federated gateways:
            - Calls Remote's /api/v1/federation/models/load endpoint
            - Waits for HTTP 2xx confirmation
            - Returns Gateway (with FederatedGateway ref)

        Post-unification: All gateways are federated.

        For federated gateways:
            - Triggers model load via FederatedLoadOrchestrator
            - Returns Gateway for forwarding

        INVARIANT: ∀ federated_gateway selected ⟹ orchestrator must be available

        ASYNC NON-BLOCKING: The orchestrator uses async httpx. Event loop
        remains responsive during remote load operations.

        Args:
            model_id: The model to load (ModelId object, parsed at API boundary)
            request: FastAPI Request for client disconnection detection
            sticky: Whether to use sticky routing

        Returns:
            Gateway for federated gateways

        INVARIANT: ∀ result: is_federated_gateway(result)

        Raises:
            HTTPException:
                404 (model not found), 503 (no gateway), 504 (timeout),
                499 (client disconnected)
        """

        # Serialize for internal dict (mock_request used for routing)
        mock_request = {"model": str(model_id)}

        # Extract request_id from request header (for tracing)
        request_id = (
            request.headers.get("X-Internal-Request-ID")
            if request is not None and hasattr(request, "headers")
            else None
        )

        # Try immediate routing first
        gateway_instance, federated_gateway = await self._attempt_immediate_route(
            model_id, mock_request, sticky=sticky
        )

        if gateway_instance:
            logger.debug(
                f"✅ Model {model_id} immediately available "
                f"on {gateway_instance.config.name}"
            )
            return gateway_instance

        if federated_gateway:
            # CRITICAL: Explicitly ensure model loaded on Remote
            # This is the key change - don't just return the gateway!
            logger.info(
                f"🔄 Federated gateway selected: {federated_gateway.name}, "
                "ensuring model loaded"
            )

            # INVARIANT CHECK: Orchestrator MUST be available if federated gateway
            # selected
            # This should be unreachable - startup fail-fast ensures orchestrator exists
            # in MASTER mode
            # If this fires, it indicates a startup wiring bug, not an operational error
            if self._federated_load_orchestrator is None:
                raise RuntimeError(
                    f"BUG: Federated gateway {federated_gateway.name} selected but "
                    "orchestrator is None. This indicates miswired startup - "
                    "startup should have failed if MASTER mode without orchestrator. "
                    "Check _initialize_federated_load_orchestrator() wiring."
                )

            await self._federated_load_orchestrator.ensure_model_loaded_on_remote(
                federated_gateway.ref,  # FederatedGateway from Gateway.ref
                model_id,
                sticky=sticky,
                request_id=request_id,
            )
            logger.info(
                f"✅ Model {model_id} confirmed loaded on {federated_gateway.name}"
            )

            # Return the Gateway wrapper (caller uses this for forwarding)
            return federated_gateway

        # Before entering retry queue, verify model exists in any gateway catalog
        logger.info(
            f"🔍 Model {model_id} not immediately available, "
            "checking if model exists..."
        )
        model_exists = await check_model_exists_anywhere(
            self._gateway_manager, model_id
        )
        if not model_exists:
            logger.error(
                f"❌ Model {model_id} not found in any gateway catalog - "
                "failing immediately"
            )
            from ....errors.model_errors import ModelErrorBuilder as ModelErrors

            raise ModelErrors.model_not_found(str(model_id))

        logger.debug(f"⏳ Model {model_id} exists but unavailable, entering queue...")
        return await await_queue_with_retry(
            model_id=model_id,
            mock_request=mock_request,
            gateway_manager=self._gateway_manager,
            loading_ops=self._loading_ops,
            get_resource_manager=self._get_resource_manager,
            config=self._config,
            attempt_immediate_route=self._attempt_immediate_route,
            request=request,
            sticky=sticky,
        )

    async def _attempt_immediate_route(
        self, model_id: ModelId, mock_request: dict[str, str], *, sticky: bool = True
    ) -> tuple["GatewayInstance | None", "Gateway | None"]:
        """
        Use DecisionEngine for gateway selection.

        Post-unification: All gateways are federated.

        Returns:
            tuple[GatewayInstance | None, Gateway | None]:
                - (None, gateway): Federated gateway selected
                - (None, None): No gateway available, should queue

        INVARIANT: ∀ result: (federated ∧ gateway) ∨ queue
        """
        # PRE-FLIGHT: Check if model exists anywhere
        model_exists = await check_model_exists_anywhere(
            self._gateway_manager, model_id
        )
        if not model_exists:
            logger.debug(
                f"Model {model_id} not found in any gateway catalog - "
                "skipping DecisionEngine selection"
            )
            return None, None

        # Use ModelRouter's decision engine
        model_router = self._gateway_manager.model_router
        gateway = await model_router.route_request(mock_request)

        if gateway:
            # All gateways are federated - return Gateway for forwarding
            logger.info(
                f"📍 Federated gateway {gateway.name} selected for {model_id} "
                f"- direct forwarding (no queue)"
            )
            return None, gateway

        return None, None

    async def _execute_eviction(
        self, gateway: "GatewayInstance", models_to_evict: list[ModelId]
    ) -> None:
        """Execute eviction plan on gateway by unloading specified models."""
        for evict_model_id in models_to_evict:
            try:
                logger.info(f"🗑️ Evicting {evict_model_id} from {gateway.config.name}")
                await self._loading_ops.unload_model(gateway, evict_model_id)
                logger.debug(f"✅ Evicted {evict_model_id} from {gateway.config.name}")
            except Exception as e:
                logger.warning(
                    f"⚠️ Failed to evict {evict_model_id} from "
                    f"{gateway.config.name}: {e}"
                )
