"""
Unified model router using Predicate-Score Pipeline.

Replaces inheritance-based CPUModelRouter/GPUModelRouter with
composition-based selection.

Architecture Benefits:
    - Robustness: Parallel collection, resilient eviction, isolated failures
    - Debugging: Function-based predicates/scorers testable in isolation
    - Traceability: SelectionResult.reason explains every decision
    - Statistics: Built-in priority hit tracking for capacity planning
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from model_id import ModelId, validate_model_id
from universal_logging import get_logger

from .eviction.planner import unload_models
from .selection.collector import build_placement
from .selection.decision import DecisionEngine, FeasibilityTier, load_routing_policy

if TYPE_CHECKING:
    from systems.federation.master.routing.forward import FederatedRequestForwarder

    from .selection.types import DecisionTrace, Gateway, Placement

logger = get_logger(__name__)


class ModelRouter:
    """
    Unified router for CPU and GPU models.

    This router abstracts model selection and request routing in a federated environment.
    It acts as the central orchestration point for determining which gateway should serve
    a given model inference request, based on current gateway status, model placement requirements,
    routing policy, and resource capacity.

    Features:
      - Predicate-Score Pipeline: Uses function-based predicates and scorers to evaluate
        all available gateways, supporting robust and traceable decision-making.
      - Parallel Gateway Collection: Collects gateway telemetry and candidate states
        in parallel for minimal latency and maximum fault tolerance.
      - Federated Routing: Supports multi-hop routing topologies (master, relay, edge stargates)
        and integrates with federation manager and eviction mechanisms for distributed model management.
      - Eviction Orchestration: Coordinates model eviction when required to free capacity
        for new model loads, delegating to the appropriate planning and execution components.
      - Routing Hysteresis: Employs sticky placement tracking to minimize spurious routing changes
        and stabilize user experience under fluctuating conditions.
      - Observability: Provides detailed statistics, selection traces, and decision telemetry
        for operational debugging and capacity planning.

    Design Invariants:
      - Selection decisions always produce a trace for auditing/diagnosis.
      - Routing respects both hard and soft affinity rules, honoring preferred placements
        where possible while falling back as policies allow.
      - Eviction and model loading are orchestrated to minimize service disruption
        and to maintain capacity guarantees.
      - Router is stateless on a per-request basis; gateway and model states
        are tracked externally.

    Typical Usage:
        router = ModelRouter(...)
        selected_gateway, trace = router._select_and_record_stats(gateways, placement, ...)
        if eviction required:
            router._execute_local_eviction(selected_gateway, trace)
        # Forward request to selected_gateway

    Summary: Routes requests based on model_id (context/profile) and gateway state.
    """

    def __init__(
        self,
        gateway_manager: Any,
        gateway_configs: dict | None = None,
        config: dict | None = None,
        event_bus: Any = None,
        federated_manager: Any = None,
        local_stargate_id: str | None = None,
        compute_type_tracker: Any = None,
        capacity_pool: Any = None,
    ):
        """
        Initialize the ModelRouter with routing policy and federation dependencies.

        Args:
            gateway_manager: Gateway manager instance for accessing local gateway state
                and model catalog information. Used by placement builder to determine
                model resource requirements.

            gateway_configs: Optional dict mapping gateway names to their configuration.
                Currently unused but preserved for backward compatibility.

            config: Routing configuration dict containing policy settings. Key fields:
                - routing.policy.affinity_weight (float, default 1.0): Weight for gateway
                  affinity scoring (prefers gateways with model already loaded).
                - routing.policy.contention_weight (float, default 1.0): Weight for
                  contention penalty (prefers less busy gateways).
                - routing.policy.eviction_weight (float, default 1.0): Weight for
                  eviction cost penalty (prefers avoiding eviction).
                - routing.policy.stability_weight (float, default 5.0): Weight for
                  sticky placement bonus (prefers previously used gateway for same model).
                - routing.policy.staleness_weight (float, default 0.5): Weight for
                  telemetry staleness penalty (prefers fresh telemetry).
                - routing.policy.eviction_margin (float, default 1.0): Score margin
                  required for T2 (eviction) to beat T1 (no eviction). Higher values
                  make eviction less likely.
                - routing.policy.stability_threshold_s (float, default 60.0): Time in
                  seconds before a sticky placement binding expires.

                If None, default policy values are used. See load_routing_policy() for
                complete schema and defaults.

            event_bus: EventBus instance for telemetry synchronization and decision
                tracing. Required for telemetry freshness waiting and routing event
                publication. If None, telemetry refresh is disabled.

            federated_manager: FederatedGatewayManager instance for accessing remote
                gateway state in federated deployments. Required for Master and Relay
                roles. If None, only local gateway routing is available (Edge mode).

            local_stargate_id: Identifier for this Stargate instance from config
                (federation.stargate_id). Required when federated_manager is provided.
                Used to distinguish local vs remote gateways and for telemetry routing.

            compute_type_tracker: MasterRequestTracker for routing key tracking
                (Master mode only). If None, routing key tracking is disabled.

        Raises:
            ValueError: If federated_manager is provided but local_stargate_id is None/empty.

        Initialization:
            - Loads routing policy from config (or defaults)
            - Creates DecisionEngine with policy and trackers
            - Initializes StickyPlacementTracker for routing hysteresis
            - Sets up TelemetryFreshnessWaiter for stale telemetry handling
            - Initializes routing statistics counters (P1-P4 hits)

        Post-Initialization:
            - Call set_load_waiter() to inject load completion waiter for eviction
            - Call set_forwarder() to inject FederatedRequestForwarder (Master mode)
            - Call configure_federation() to update federation settings if needed
        """
        self.gateway_manager = gateway_manager
        self.gateway_configs = gateway_configs or {}
        self._load_waiter = None
        self._event_bus = event_bus

        # Federation dependencies
        self._federated_manager = federated_manager
        self._local_stargate_id = local_stargate_id or "local"

        # Forwarder for federated eviction (Master mode only)
        self._forwarder: FederatedRequestForwarder | None = None

        # Request tracker for compute-type limits (Master mode only)
        self._compute_type_tracker = compute_type_tracker

        # Capacity pool for admission control (Master mode only)
        self._capacity_pool = capacity_pool

        # Fail-fast: federation manager requires stargate_id
        if federated_manager is not None and not local_stargate_id:
            raise ValueError(
                "local_stargate_id is required when federated_manager is provided. "
                "Configure federation.stargate_id in stargate_config.yaml"
            )

        # Telemetry freshness waiter (epoch-based, no race conditions)
        from .telemetry import TelemetryFreshnessWaiter

        self._telemetry_waiter = TelemetryFreshnessWaiter(event_bus=event_bus)

        # Stability tracker for routing hysteresis
        from .selection.decision import StickyPlacementTracker

        self._stability_tracker = StickyPlacementTracker()

        # Routing policy and decision engine
        self._routing_policy = load_routing_policy(config or {})
        self._decision_engine = DecisionEngine(
            self._routing_policy,
            event_bus=event_bus,
            routing_key_tracker=compute_type_tracker,
            # Admission control: CapacityPool in systems/routing/capacity/
        )

        # Routing statistics
        self.stats = {
            "total_requests": 0,
            "priority_1_hits": 0,
            "priority_2_hits": 0,
            "priority_3_hits": 0,
            "priority_4_hits": 0,
        }

    def set_load_waiter(self, load_waiter) -> None:
        """Inject load_waiter for event-driven eviction confirmation."""
        self._load_waiter = load_waiter

    def configure_federation(
        self,
        federated_manager: Any,
        local_stargate_id: str,
    ) -> None:
        """
        Configure federation dependencies after initialization.

        CRITICAL: Does NOT reset the router - preserves load_waiter and other state.

        Args:
            federated_manager: FederatedGatewayManager instance
            local_stargate_id: This Stargate's ID from config

        Raises:
            ValueError: If local_stargate_id is empty/None
        """
        if not local_stargate_id:
            raise ValueError(
                "local_stargate_id is required for federation. "
                "Configure federation.stargate_id in stargate_config.yaml"
            )

        self._federated_manager = federated_manager
        self._local_stargate_id = local_stargate_id

        logger.info(
            f"Federation configured: stargate_id={local_stargate_id}, "
            f"federated_manager={'enabled' if federated_manager else 'disabled'}"
        )

    def set_forwarder(self, forwarder: FederatedRequestForwarder) -> None:
        """
        Inject FederatedRequestForwarder for eviction execution.

        Called by Stargate startup after federation initialization (Master mode).
        """
        self._forwarder = forwarder
        logger.info("✅ ModelRouter wired with forwarder for federated eviction")

    # -------------------------------------------------------------------------
    # Request Routing - SRP Helpers
    # -------------------------------------------------------------------------

    def _extract_model_id(self, request: dict | Any) -> str | None:
        """Extract model_id from request dict or object."""
        if isinstance(request, dict):
            return request.get("model")
        return getattr(request, "model", None)

    def _validate_and_parse_model(self, model_id: str) -> tuple[ModelId, str] | None:
        """
        Validate model_id and return parsed ModelId + routing_key.

        Returns:
            (ModelId, routing_key) if valid, None if invalid
        """
        validation_error = validate_model_id(model_id)
        if validation_error:
            logger.error(f"Invalid model ID {model_id}: {validation_error}")
            return None

        parsed = ModelId.parse(model_id)
        return (parsed, parsed.routing_key)

    def _collect_candidate_gateways(self) -> list[Gateway]:
        """
        Collect federated gateways as Gateway snapshots.

        Post-unification: All gateways accessed via federation.

        Returns:
            List of Gateway objects for DecisionEngine
        """
        from .selection.stargate_collector import collect_stargates, stargate_to_gateway

        stargates = collect_stargates(
            _local_stargate_id=self._local_stargate_id,
            federated_manager=self._federated_manager,
        )

        return [stargate_to_gateway(sg) for sg in stargates]

    async def _refresh_stale_candidates(self, gateways: list[Gateway]) -> list[Gateway]:
        """
        Wait for fresh telemetry if any gateways are stale, then re-collect.

        Args:
            gateways: Current gateway snapshots

        Returns:
            Refreshed gateway list (or original if no refresh needed/timeout)
        """
        max_telemetry_age_ms = 1000  # 1 second threshold
        stale_gateways = [
            gw
            for gw in gateways
            if gw.telemetry_timestamp > 0
            and gw.is_telemetry_stale(max_telemetry_age_ms)
        ]

        if not stale_gateways or not self._event_bus:
            return gateways

        logger.debug(f"Telemetry stale on {len(stale_gateways)} gateways, waiting...")

        if await self._telemetry_waiter.wait_for_telemetry_update(timeout_s=0.5):
            logger.debug("✅ Received fresh telemetry update")
            return self._collect_candidate_gateways()

        logger.warning(
            f"⚠️ Telemetry sync timed out after 500ms "
            f"(stale on {len(stale_gateways)} gateways), using cached data"
        )
        return gateways

    def _log_routing_candidates(self, gateways: list[Gateway]) -> None:
        """
        Log candidate gateways for diagnostics.

        Post-unification: All gateways are federated.
        """
        logger.info(f"🔍 Routing candidates: {len(gateways)} gateways")
        for gw in gateways:
            logger.debug(
                f"  - {gw.name} (federated): active_requests={gw.active_requests}"
            )

    def _select_and_record_stats(
        self,
        gateways: list[Gateway],
        placement: Placement,
        request_id: str | None,
    ) -> tuple[Gateway | None, DecisionTrace]:
        """
        Run decision engine and record stats.

        Returns:
            (selected_gateway, trace)
        """
        selected, trace = self._decision_engine.select(
            gateways,
            placement,
            request_id,
            stability_tracker=self._stability_tracker,
        )

        if selected:
            if trace.selection_tier == FeasibilityTier.T1_FEASIBLE_NOW:
                # Check if warm (model loaded) or cold (needs loading)
                is_warm = any(
                    c.score_components and c.score_components.warm > 0
                    for c in trace.candidates
                    if c.gateway.name == selected.name
                )
                if is_warm:
                    self.stats["priority_1_hits"] += 1
                else:
                    self.stats["priority_2_hits"] += 1
            elif trace.selection_tier == FeasibilityTier.T2_FEASIBLE_EVICT:
                self.stats["priority_3_hits"] += 1
        else:
            self.stats["priority_4_hits"] += 1

        return selected, trace

    async def _execute_local_eviction(
        self, selected: Gateway, trace: DecisionTrace
    ) -> bool:
        """
        Execute eviction plan before load.

        Delegates to shared eviction executor.

        Args:
            selected: Selected gateway (FederatedGateway ref)
            trace: Decision trace containing candidates with eviction plans

        Returns:
            True if eviction succeeded (or no eviction needed), False otherwise
        """
        from .eviction.executor import (
            execute_eviction_plan,
            get_eviction_plan_for_gateway,
        )

        eviction_plan = get_eviction_plan_for_gateway(trace, selected.name)

        # No eviction needed?
        if eviction_plan is None or not eviction_plan.models_to_evict:
            return True

        # Get FederatedGateway from ref
        from systems.federation.common.types import FederatedGateway

        if not isinstance(selected.ref, FederatedGateway):
            logger.warning(
                f"Cannot execute eviction: gateway ref is not FederatedGateway "
                f"(got {type(selected.ref).__name__})"
            )
            return False

        # Need forwarder to send unload requests
        if not self._forwarder:
            logger.error(
                "❌ Cannot execute eviction: no forwarder configured. "
                "Eviction requires FederatedRequestForwarder (Master mode)."
            )
            return False

        return await execute_eviction_plan(
            forwarder=self._forwarder,
            federated_gateway=selected.ref,
            eviction_plan=eviction_plan,
            gateway_name=selected.name,
            event_bus=self._event_bus,
        )

    # -------------------------------------------------------------------------
    # Main Entry Point
    # -------------------------------------------------------------------------

    async def route_request(self, request: dict | Any) -> Gateway | None:
        """
        Route request to best gateway (federated).

        Post-unification: All gateways accessed via federation.

        Returns:
            Gateway if routing succeeded, None if should queue.

            Callers forward to remote Stargate (gateway.ref is FederatedGateway).
        """
        self.stats["total_requests"] += 1

        # 1. Extract and validate model
        model_id = self._extract_model_id(request)
        if not model_id:
            logger.error("No model specified in request")
            return None

        parsed_result = self._validate_and_parse_model(model_id)
        if not parsed_result:
            return None
        parsed, routing_key = parsed_result

        # 2. Build placement
        placement = await build_placement(
            parsed, self.gateway_manager, original_model_id=model_id
        )
        if not placement:
            return None

        logger.debug(
            f"Routing {placement.model_id} (from {model_id}): "
            f"RAM={placement.ram_mb}MB, VRAM={placement.vram_mb}MB, "
            f"is_gpu={placement.is_gpu}"
        )

        # 3. Collect candidates (local + federated)
        gateways = self._collect_candidate_gateways()

        # 4. Refresh if stale
        gateways = await self._refresh_stale_candidates(gateways)

        if not gateways:
            logger.warning("No healthy gateways available")
            self.stats["priority_4_hits"] += 1
            return None

        # 5. Log candidates
        self._log_routing_candidates(gateways)

        # 6. Select gateway
        request_id = request.get("_request_id") if isinstance(request, dict) else None
        selected, trace = self._select_and_record_stats(
            gateways, placement, request_id
        )

        if not selected:
            logger.debug(f"P4: No gateway available for {model_id} - queued")
            return None

        # 6a. Admission control: acquire slot before proceeding
        # Admission control: CapacityPool in systems/routing/capacity/
        if self._capacity_pool:
            is_sticky = request.get("sticky") if isinstance(request, dict) else False
            if is_sticky:
                allowed_gateway_ids = frozenset({selected.name})
            else:
                allowed_gateway_ids = frozenset(
                    g.name for g in gateways
                    if parsed in g.loaded_models
                )

            timeout_s = 30.0

            try:
                token = await self._capacity_pool.acquire_token(
                    request_id=request_id or "unknown",
                    model_id=parsed.routing_key,
                    allowed_gateway_ids=allowed_gateway_ids,
                    timeout_s=timeout_s,
                )

                if token.gateway_id != selected.name:
                    original_gateway_name = selected.name
                    selected = next(
                        (g for g in gateways if g.name == token.gateway_id),
                        selected,
                    )
                    logger.info(
                        f"📊 Admission control reassigned {model_id} from "
                        f"{original_gateway_name} → {token.gateway_id}"
                    )
            except Exception as e:
                logger.error(f"❌ Capacity pool acquire failed: {e}")
                return None

        # 7. Execute eviction (federated)
        eviction_ok = await self._execute_local_eviction(selected, trace)
        if not eviction_ok:
            logger.warning(
                f"⚠️ Eviction failed for {model_id} on {selected.name}, "
                "will retry routing"
            )
            return None  # Return to queue, retry later

        return selected

    # -------------------------------------------------------------------------
    # Supporting Methods
    # -------------------------------------------------------------------------

    async def _is_cpu_model(self, model_id: str) -> bool:
        """
        Check if model is CPU-only.

        Required by: ResourceVerifier
        """
        from model_id import ModelId

        parsed = ModelId.parse(model_id)
        placement = await build_placement(parsed, self.gateway_manager)
        return placement is not None and not placement.is_gpu

    async def _is_gpu_model(self, model_id: str) -> bool:
        """
        Check if model uses GPU.

        Required by: ResourceVerifier
        """
        return not await self._is_cpu_model(model_id)

    def _is_hybrid_request(self, routing_model_id: str, original_model_id: str) -> bool:
        """Determine if this request is for a hybrid model."""
        return "-hybrid" in original_model_id

    async def _execute_eviction(
        self, gateway_instance, models_to_evict: list[str]
    ) -> bool:
        """Execute eviction plan."""
        if not models_to_evict:
            return True
        return await unload_models(
            self._load_waiter,
            gateway_instance,
            models_to_evict,
        )

    def log_routing_stats(self) -> None:
        """Log current routing statistics."""
        total = self.stats["total_requests"]
        if total == 0:
            return

        p1 = self.stats["priority_1_hits"]
        p2 = self.stats["priority_2_hits"]
        p3 = self.stats["priority_3_hits"]
        p4 = self.stats["priority_4_hits"]
        logger.info(
            f"Routing stats: total={total}, "
            f"P1={p1} ({p1 / total:.1%}), "
            f"P2={p2} ({p2 / total:.1%}), "
            f"P3={p3} ({p3 / total:.1%}), "
            f"P4={p4} ({p4 / total:.1%})"
        )
