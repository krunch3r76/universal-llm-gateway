"""
BatchRouter - Atomic routing for inference batches.

Routes entire batches atomically, ensuring coordinated resource
allocation for parallel pipeline steps.

Phase 6 Enhancements:
- BatchModelTracker for cross-batch coordination
- SchedulingScorer for topology-aware prioritization
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .batch import InferenceBatch
from .request import InferenceRequest

if TYPE_CHECKING:
    from gateways import SingleGatewayManager
    from systems.routing.selection.types import Gateway

    from ..single.operations import ModelRoutingOperations
    from .model_tracker import BatchModelTracker
    from .scheduling_scorer import SchedulingScorer

logger = get_logger(__name__)


class BatchRouter:
    """
    Routes inference batches atomically.

    For pipeline batches: coordinates all requests together,
    ensuring resources are available or triggering eviction.

    For standalone batches: fast path through existing routing.

    Phase 6 Dependencies (injected):
    - model_tracker: Cross-batch model load coordination
    - scheduler_scorer: Topology-aware prioritization
    """

    def __init__(
        self,
        gateway_manager: SingleGatewayManager,
        routing_ops: ModelRoutingOperations,
        model_tracker: BatchModelTracker | None = None,
        scheduler_scorer: SchedulingScorer | None = None,
    ):
        """
        Initialize batch router.

        Args:
            gateway_manager: Gateway management
            routing_ops: Routing operations
            model_tracker: Optional cross-batch tracker (Phase 6)
            scheduler_scorer: Optional scheduler scorer (Phase 6)
        """
        self._gateway_manager = gateway_manager
        self._routing_ops = routing_ops
        self._model_tracker = model_tracker
        self._scheduler_scorer = scheduler_scorer

    async def route_batch(self, batch: InferenceBatch) -> InferenceBatch:
        """Route all requests in batch."""
        if batch.is_standalone:
            return await self._route_standalone(batch)
        return await self._route_pipeline_batch(batch)

    async def route_batch_dict(self, batch_data: dict) -> dict:
        """Route a batch specified as a dict (domain-agnostic interface)."""
        from .batch import InferenceBatchFactory

        batch = InferenceBatchFactory.from_dict(batch_data)
        routed_batch = await self.route_batch(batch)

        return {
            "gateway_assignments": routed_batch.gateway_assignments,
            "deferred_requests": routed_batch.deferred_requests,
        }

    async def _route_standalone(self, batch: InferenceBatch) -> InferenceBatch:
        """
        Fast path for standalone single-request batches.

        Handles both local and federated gateway assignments.
        """
        if not batch.requests:
            logger.debug(f"Empty standalone batch {batch.batch_id} - no-op routing")
            return batch

        request = batch.requests[0]

        try:
            # Handle tuple return (gateway_instance, federated_gateway)
            (
                gateway_instance,
                federated_gateway,
            ) = await self._routing_ops._attempt_immediate_route(
                request.model_id,
                {"model": str(request.model_id)},
                sticky=request.sticky,
            )

            if gateway_instance:
                # Local gateway
                batch.gateway_assignments[request.request_id] = (
                    gateway_instance.config.name
                )
            elif federated_gateway:
                # Federated gateway - store gateway name for forwarding
                batch.gateway_assignments[request.request_id] = federated_gateway.name
                # FIXED: Field always exists - removed hasattr shim
                batch.federated_assignments[request.request_id] = federated_gateway

        except Exception as e:
            logger.warning(f"Standalone routing failed for {request.model_id}: {e}")

        return batch

    async def _route_pipeline_batch(self, batch: InferenceBatch) -> InferenceBatch:
        """
        Route pipeline batch with resource coordination.

        Uses Phase 6 components when available:
        - BatchModelTracker for cross-batch coordination
        - SchedulingScorer for topology-aware prioritization
        """
        if not batch.requests:
            logger.debug(f"Empty pipeline batch {batch.batch_id} - no-op routing")
            return batch

        from .cold_routing import assign_cold_requests
        from .warm_routing import assign_warm_requests

        # Collect current gateway state
        gateways = await self._collect_gateway_state()

        if not gateways:
            logger.warning("No healthy gateways available for batch routing")
            for request in batch.requests:
                batch.deferred_requests.append(request.request_id)
            return batch

        # Partition requests into warm (model loaded) and cold (needs load)
        warm_requests, cold_requests = self._partition_by_model_loaded_state(
            batch.requests, gateways
        )

        logger.info(
            f"Batch {batch.batch_id}: {len(warm_requests)} warm, "
            f"{len(cold_requests)} cold requests"
        )

        # Assign warm requests immediately (no resource contention)
        await assign_warm_requests(warm_requests, gateways, batch)

        # Assign cold requests with resource coordination (Phase 6 enhanced)
        if cold_requests:
            await assign_cold_requests(
                cold_requests,
                gateways,
                batch,
                self._gateway_manager,
                self._routing_ops,
                model_tracker=self._model_tracker,
                scheduler_scorer=self._scheduler_scorer,
            )

        return batch

    async def _collect_gateway_state(self) -> list[Gateway]:
        """Collect current state of local gateway."""
        from systems.routing.selection.collector import collect_gateways

        gateway = self._gateway_manager.get_gateway()
        if not gateway:
            return []

        return await collect_gateways(
            [gateway],
            include_model_details=True,
            gateway_manager=self._gateway_manager,
        )

    def _partition_by_model_loaded_state(
        self,
        requests: list[InferenceRequest],
        gateways: list[Gateway],
    ) -> tuple[list[tuple[InferenceRequest, str]], list[InferenceRequest]]:
        """Partition requests by whether model is already loaded."""
        warm: list[tuple[InferenceRequest, str]] = []
        cold: list[InferenceRequest] = []

        for request in requests:
            gateway_name = self._find_gateway_hosting_model(request.model_id, gateways)
            if gateway_name:
                warm.append((request, gateway_name))
            else:
                cold.append(request)

        return warm, cold

    def _find_gateway_hosting_model(
        self,
        model_id: ModelId,
        gateways: list[Gateway],
    ) -> str | None:
        """
        Find gateway where model is already loaded.

        INVARIANT: ∀ gateway: loaded_models ⊆ frozenset[ModelId]
        Uses ModelId equality (handles -hybrid normalization automatically).
        """
        for gateway in gateways:
            # ModelId.__eq__ handles normalization (-hybrid stripping)
            for loaded in gateway.loaded_models:
                if model_id == loaded:
                    return gateway.name

        return None
