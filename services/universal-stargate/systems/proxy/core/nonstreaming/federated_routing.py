"""
Federated gateway routing for router-only mode.

Handles gateway selection, model loading, and routing events.
"""

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.federation.common.config.schema import EndpointCategory

from ..endpoint_category import derive_endpoint_category
from .selection_errors import (
    raise_capacity_error,
    raise_eviction_failed_error,
    raise_gateway_capacity_error,
    raise_model_unavailable_error,
    raise_no_gateways_error,
)

if TYPE_CHECKING:
    from .context import RequestContext

logger = get_logger(__name__)


async def _route_to_federated_gateway(
    context: "RequestContext",
    federated_manager,
    federated_load_orchestrator,
    federation_forwarder,
    event_bus,
    routing_start_time: float,
    routing_config: dict | None = None,
    stability_tracker=None,
    compute_type_tracker=None,
    routing_key_tracker=None,
    admission_queue=None,
) -> tuple[str | None, str | None]:
    """
    Router-only mode: Select and load model on federated gateway.

    Args:
        context: Request context
        federated_manager: FederatedGatewayManager for getting remote gateways
        federated_load_orchestrator: For loading models on remotes
        federation_forwarder: Forwarder for sending eviction commands (may be None)
        event_bus: Event bus for routing events
        routing_start_time: Timestamp when routing started
        routing_config: Full Stargate config dict for loading routing policy
        compute_type_tracker: MasterRequestTracker for compute-type limits
        routing_key_tracker: MasterRequestTracker for eviction protection

    Returns:
        Tuple of (gateway_name, reservation_id) if selected, (None, None) otherwise

    Raises:
        HTTPException: If no gateway available or model not found
    """
    from systems.routing.selection.types import Placement

    model_id = context.selected_model

    # Use pre-set endpoint category if available (e.g., embedding requests
    # with no http_request). Otherwise derive from request path.
    if context.routing_endpoint_category is not None:
        endpoint_category = context.routing_endpoint_category
        logger.debug(f"Using pre-set endpoint category: {endpoint_category.value}")
    else:
        try:
            endpoint_category = derive_endpoint_category(request=context.http_request)
        except ValueError:
            # Fallback for unknown paths - log at ERROR per quality-gates policy
            logger.error(
                "❌ Could not derive endpoint category from request, "
                "defaulting to generation (this may cause capacity tracking issues)"
            )
            endpoint_category = EndpointCategory.GENERATION

        # Store in context for consistent use during forwarding
        # CRITICAL: Prevents leak when reservation category differs from forward
        context.routing_endpoint_category = endpoint_category

    # Timing marker: routing start
    selection_start_ms = int(time.time() * 1000)
    logger.info(f"ROUTING START: {model_id} at {selection_start_ms}ms")

    logger.info(f"🔍 Router-only: selecting federated gateway for {model_id}")

    # INV-2: stability_tracker is REQUIRED - fail loudly if missing
    if stability_tracker is None:
        raise ValueError(
            "stability_tracker is required for routing stability. "
            "Ensure component_factory properly initializes StickyPlacementTracker."
        )

    # Get federated gateways
    all_gateways = federated_manager.get_all_gateways()
    logger.info(f"🔍 Router-only: Total gateways registered: {len(all_gateways)}")
    for gw in all_gateways:
        logger.info(
            f"  - {gw.gateway_id}: age={gw.telemetry_age_ms}ms, "
            f"unreachable={gw.is_unreachable}"
        )

    federated_gateways = federated_manager.get_healthy_gateways()
    logger.info(f"🔍 Router-only: Healthy gateways: {len(federated_gateways)}")
    if not federated_gateways:
        logger.error("No federated gateways available in router-only mode")
        raise_no_gateways_error()

    # Convert FederatedGateway to Gateway for DecisionEngine
    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )

    gateways_for_routing = federated_gateways_to_routing_candidates(federated_gateways)

    # Exclude gateways that failed on previous retry (keep all if none remain)
    if context.excluded_gateway_ids:
        kept = [
            g
            for g in gateways_for_routing
            if g.name not in context.excluded_gateway_ids
        ]
        if kept:
            logger.info("🚫 Routing: excluded %s", context.excluded_gateway_ids)
            gateways_for_routing = kept

    logger.debug(
        f"Router-only: {len(gateways_for_routing)} federated gateways available"
    )

    # Build placement hint from first matching gateway's model_resources.
    # This is a hint only — per-gateway authoritative figures are resolved
    # in _check_resources() via resolve_gateway_requirements().
    vram_mb = 0
    ram_mb = 0
    for fg in federated_gateways:
        if model_id in fg.model_resources:
            resources = fg.model_resources[model_id]
            vram_mb = resources.get("vram_usage", 0)
            ram_mb = resources.get("ram_usage", 0)
            break

    placement = Placement(
        model_id=model_id,
        ram_mb=ram_mb,
        vram_mb=vram_mb,
        is_gpu=vram_mb > 0,
        endpoint_category=endpoint_category.value,
    )

    logger.info(
        f"📋 Router-only: Placement hint for {model_id}: "
        f"VRAM={placement.vram_mb}MB, RAM={placement.ram_mb}MB, "
        f"is_gpu={placement.is_gpu} (per-gateway figures resolved at check time)"
    )

    # Create DecisionEngine (stateless, can be per-request OK)
    # INV-1: routing_config must be FULL config dict
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.config import load_routing_policy

    policy = load_routing_policy(routing_config or {})
    decision_engine = DecisionEngine(
        policy=policy,
        event_bus=event_bus,
        routing_key_tracker=routing_key_tracker,
        # Admission control: CapacityLedger in systems/routing/capacity/
    )

    # Use DecisionEngine to select gateway
    # Log detailed gateway state for nonsticky debugging
    for g in gateways_for_routing:
        logger.info(
            f"Gateway {g.name}: loaded={len(g.loaded_models)}, "
            f"loading={len(g.loading_models)}, "
            f"target_model_loading={model_id in g.loading_models}"
        )

    logger.info(
        f"📋 Router-only candidates: {len(gateways_for_routing)} gateways, "
        f"empty={sum(1 for g in gateways_for_routing if len(g.loaded_models) + len(g.loading_models) == 0)}, "  # noqa: E501
        f"with_model={sum(1 for g in gateways_for_routing if model_id in g.loaded_models)}, "  # noqa: E501
        f"loading_model={sum(1 for g in gateways_for_routing if model_id in g.loading_models)}"  # noqa: E501
    )

    selected_gateway, trace = decision_engine.select(
        gateways=gateways_for_routing,
        placement=placement,
        request_id=context.request_id,
        sticky=context.model_sticky,
        stability_tracker=stability_tracker,
    )

    # Admission control: acquire slot before proceeding
    # CRITICAL: This must happen AFTER select() and BEFORE any await
    if selected_gateway and admission_queue:
        # Determine allowed gateways for admission
        if context.model_sticky:
            # Sticky: only allow the selected gateway
            allowed_gateway_ids = frozenset({selected_gateway.name})
        else:
            # Non-sticky: allow any gateway that has the model
            allowed_gateway_ids = frozenset(
                g.name for g in gateways_for_routing if model_id in g.loaded_models
            )

        # Acquire slot (may await if all gateways at capacity)
        # Timeout from config or default 30s
        timeout_s = (
            routing_config.get("admission", {}).get("timeout_s", 30.0)
            if routing_config
            else 30.0
        )

        try:
            assigned_gateway_id = await admission_queue.acquire(
                request_id=context.request_id,
                model_id=model_id.routing_key,
                allowed_gateway_ids=allowed_gateway_ids,
                timeout_s=timeout_s,
            )

            # If assigned to a different gateway than selected, re-select
            if assigned_gateway_id != selected_gateway.name:
                original_gateway_name = selected_gateway.name
                selected_gateway = next(
                    (g for g in gateways_for_routing if g.name == assigned_gateway_id),
                    selected_gateway,
                )
                logger.info(
                    f"📊 Admission control reassigned {model_id} from "
                    f"{original_gateway_name} → {assigned_gateway_id}"
                )
        except TimeoutError:
            logger.warning(
                f"⏳ Admission queue timeout: model={model_id.routing_key} "
                f"gateway={selected_gateway.name} timeout_s={timeout_s} "
                f"allowed_gateways={sorted(allowed_gateway_ids)}"
            )
            raise_gateway_capacity_error(selected_gateway.name)
        except Exception as e:
            logger.error(f"❌ Admission queue acquire failed: {e}")
            raise_gateway_capacity_error(selected_gateway.name)

    # Emit orchestrator decision event
    if event_bus:
        import asyncio

        from src.scheduling.events import FederationOrchestratorDecided

        decision_type = "route" if selected_gateway else "reject"
        target = selected_gateway.name if selected_gateway else None
        reason = (
            f"Selected {selected_gateway.name} (tier={trace.selection_tier.name})"
            if selected_gateway
            else "No feasible gateway available"
        )
        alternatives = (
            [g.name for g in gateways_for_routing[:5]] if gateways_for_routing else []
        )

        asyncio.create_task(
            event_bus.publish_async_nowait(
                FederationOrchestratorDecided(
                    request_id=context.request_id,
                    decision_type=decision_type,
                    target=target,
                    reason=reason,
                    alternatives_considered=alternatives if alternatives else None,
                )
            )
        )

    # CRITICAL: Synchronous optimistic mark - no await, immediate visibility
    # Must happen before ANY await to prevent concurrent select() seeing stale state
    marked_loading = False
    optimistic_mark_gateway_id = None
    optimistic_mark_model_id = None

    if (
        selected_gateway
        and federated_manager
        and model_id not in selected_gateway.loaded_models
    ):
        marked_loading = federated_manager.mark_loading_optimistic(
            selected_gateway.ref.gateway_id, model_id
        )
        if marked_loading:
            optimistic_mark_gateway_id = selected_gateway.ref.gateway_id
            optimistic_mark_model_id = model_id

    # Timing markers AFTER marking (so concurrent requests see the mark)
    selection_end_ms = int(time.time() * 1000)
    if marked_loading:
        logger.info(
            f"ROUTING+MARK: {model_id} → {selected_gateway.name} "
            f"at {selection_end_ms}ms "
            f"(took {selection_end_ms - selection_start_ms}ms, marked loading)"
        )
    else:
        logger.info(
            f"ROUTING END: {model_id} selected "
            f"{selected_gateway.name if selected_gateway else 'NONE'} "
            f"at {selection_end_ms}ms (took {selection_end_ms - selection_start_ms}ms)"
        )

    if not selected_gateway:
        logger.error(f"No feasible federated gateway for {model_id}")

        # Emit routing rejected event
        if event_bus:
            import asyncio

            from src.scheduling.events import FederationRoutingRejected

            asyncio.create_task(
                event_bus.publish_async_nowait(
                    FederationRoutingRejected(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        reason="No feasible gateway available",
                    )
                )
            )

        # For sticky models: Check if we should wait (model at capacity)
        # vs error (model doesn't exist)
        if context.model_sticky and trace and trace.candidates:
            # Check if ANY gateway fails capacity constraints
            # Includes: compute_type limits, per-gateway capacity, resources
            # Resource constraints (has_enough_vram/ram) indicate temporarily
            # at capacity (e.g., loading models consuming resources)
            capacity_constraints = {
                "compute_type_capacity",
                "has_gateway_capacity",
                "has_enough_vram",
                "has_enough_ram",
            }
            has_capacity_failure = any(
                any(f.constraint in capacity_constraints for f in c.constraints_failed)
                for c in trace.candidates
            )

            if has_capacity_failure:
                # Find details for error envelope data
                capacity_gateway_url = None
                capacity_details: dict[str, Any] = {"model_id": str(model_id)}

                # Extract capacity details from failures
                for c in trace.candidates:
                    for f in c.constraints_failed:
                        if f.constraint in capacity_constraints:
                            capacity_details.update(f.details)
                            break

                # Find gateway URL for wait monitoring
                # Check loaded_models first (model already running)
                # Then check catalog_models (model exists but not loaded yet)
                logger.debug(
                    f"🔍 Searching for {model_id} (type={type(model_id).__name__}, "
                    f"repr={repr(model_id)}) across {len(federated_gateways)} gateways"
                )
                for fg in federated_gateways:
                    logger.debug(
                        f"  Gateway {fg.gateway_id}: "
                        f"loaded={len(fg.loaded_models)}, "
                        f"available={len(fg.available_models)}"
                    )
                    if model_id in fg.loaded_models:
                        capacity_gateway_url = fg.remote_stargate_url
                        capacity_details["gateway_url"] = capacity_gateway_url
                        logger.debug(
                            f"Found loaded model {model_id} on {fg.gateway_id} "
                            f"(URL: {capacity_gateway_url})"
                        )
                        break

                if not capacity_gateway_url:
                    # Model not loaded - check available_models (catalog)
                    # available_models = ALL models in catalog that CAN be loaded
                    for fg in federated_gateways:
                        if model_id in fg.available_models:
                            capacity_gateway_url = fg.remote_stargate_url
                            capacity_details["gateway_url"] = capacity_gateway_url
                            logger.debug(
                                f"Found cataloged model {model_id} on {fg.gateway_id} "
                                f"(URL: {capacity_gateway_url}, not yet loaded)"
                            )
                            break
                        else:
                            # Diagnostic: show why not found
                            if fg.available_models:
                                sample = list(fg.available_models)[:3]
                                sample_repr = [repr(m) for m in sample]
                                logger.debug(
                                    f"  {fg.gateway_id}: model not in "
                                    f"available_models. Sample: {sample_repr}"
                                )
                            else:
                                logger.debug(
                                    f"  {fg.gateway_id}: available_models is empty"
                                )

                # Model exists but at capacity - raise with error_envelope
                # NOTE: With proactive queueing, this path is only hit when:
                # 1. Model was just loaded (no queue existed yet)
                # 2. TOCTOU race between queue and routing
                if not capacity_gateway_url:
                    logger.error(
                        f"❌ BUG: Capacity constraint failed for {model_id} but "
                        f"gateway_url not found. This should not happen if model "
                        f"exists in available_models. Check diagnostic logs above."
                    )
                logger.info(
                    f"⏳ Sticky model {model_id} at capacity on "
                    f"{capacity_gateway_url or 'UNKNOWN'} (reactive fallback path)"
                )
                raise_capacity_error(str(model_id), capacity_details)

        # Model doesn't exist anywhere or non-sticky - structured error
        raise_model_unavailable_error(str(model_id))

    logger.info(
        f"📍 ROUTING (router-only): model={model_id} "
        f"gateway={selected_gateway.name} route_type=federated "
        f"tier={trace.selection_tier.name}"
    )

    try:
        # Execute eviction if needed (T2_FEASIBLE_EVICT tier)
        from systems.routing.selection.decision import FeasibilityTier

        from .eviction_execution import execute_router_only_eviction

        if trace.selection_tier == FeasibilityTier.T2_FEASIBLE_EVICT:
            eviction_ok = await execute_router_only_eviction(
                federation_forwarder=federation_forwarder,
                federated_manager=federated_manager,
                selected_gateway=selected_gateway,
                trace=trace,
                request_id=context.request_id,
                event_bus=event_bus,
            )
            if not eviction_ok:
                logger.warning(
                    f"⚠️ Eviction failed for {model_id} on {selected_gateway.name}"
                )
                raise_eviction_failed_error(
                    str(model_id),
                    selected_gateway.name,
                    gateway_url=selected_gateway.ref.remote_stargate_url,
                )

        # Load model on remote gateway
        if federated_load_orchestrator:
            await federated_load_orchestrator.ensure_model_loaded_on_remote(
                selected_gateway.ref,  # FederatedGateway
                model_id,
                sticky=context.model_sticky,
                request_id=context.request_id,
            )
            logger.info(f"✅ Model {model_id} loaded on {selected_gateway.name}")
        else:
            logger.warning(
                f"No federated_load_orchestrator available, "
                f"assuming model {model_id} already loaded"
            )

        # Set context - is_federated and federated_gateway are computed properties
        context.selected_gateway = selected_gateway

        # Emit routing event
        if event_bus and context.selected_gateway:
            routing_time_ms = (time.time() - routing_start_time) * 1000
            try:
                from src.scheduling.events import RequestRouted

                # Gateway.ref is FederatedGateway with remote_stargate_url
                gateway_url = getattr(
                    context.selected_gateway.ref, "remote_stargate_url", "unknown"
                )

                await event_bus.publish_async_nowait(
                    RequestRouted(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        gateway_url=gateway_url,
                        gateway_name=context.selected_gateway.name,
                        timestamp=time.time(),
                        routing_time_ms=routing_time_ms,
                        immediate_route=True,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to emit REQUEST_ROUTED event: {e}")

        return (selected_gateway.name, None)

    except Exception:
        # Clear optimistic mark on any failure
        if (
            optimistic_mark_gateway_id
            and optimistic_mark_model_id
            and federated_manager
        ):
            await federated_manager.clear_model_loading(
                optimistic_mark_gateway_id, optimistic_mark_model_id
            )
        raise
