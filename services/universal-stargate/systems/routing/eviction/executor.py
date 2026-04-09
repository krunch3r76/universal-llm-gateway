"""
Eviction execution for federated gateways.

Shared by ModelRouter and router-only routing code.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.federation.common.types import FederatedGateway
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.selection.decision.types import EvictionPlanSummary
    from systems.routing.selection.types import DecisionTrace

logger = get_logger(__name__)
_inflight_evictions: dict[tuple[str, tuple[str, ...]], asyncio.Task[bool]] = {}


def get_eviction_plan_for_gateway(
    trace: DecisionTrace,
    gateway_name: str,
) -> EvictionPlanSummary | None:
    """
    Extract eviction plan for a specific gateway from DecisionTrace.

    CRITICAL: eviction_plan is on GatewayCandidate, not DecisionTrace.

    Args:
        trace: Decision trace containing candidates
        gateway_name: Name of selected gateway

    Returns:
        EvictionPlanSummary if found, None otherwise
    """
    for candidate in trace.candidates:
        if candidate.gateway.name == gateway_name:
            return candidate.eviction_plan
    return None


async def execute_eviction_plan(
    forwarder: FederatedRequestForwarder,
    federated_gateway: FederatedGateway,
    eviction_plan: EvictionPlanSummary | None,
    gateway_name: str,
    request_id: str | None = None,
    event_bus=None,
) -> bool:
    """
    Execute eviction plan by sending unload commands to Remote.

    INVARIANT: ∀ model ∈ eviction_plan: unloaded before load request sent
    INVARIANT: ∀ unload: (HTTP ok ∧ MODEL_UNLOADED event) ∨ abort

    Uses unified event-driven waiting (same as local eviction):
    1. Register wait handle (BEFORE HTTP to prevent race)
    2. Send HTTP unload request
    3. Wait for MODEL_UNLOADED event from EventBus
    4. Only return success when event confirms resources freed

    Args:
        forwarder: Request forwarder for sending unload commands
        federated_gateway: Target gateway (FederatedGateway ref)
        eviction_plan: Plan with models to evict (None = no eviction)
        gateway_name: Gateway name for logging
        request_id: Parent request ID for tracing
        event_bus: EventBus for subscribing to MODEL_UNLOADED events

    Returns:
        True if eviction succeeded (or no eviction needed), False otherwise
    """
    if eviction_plan is None or not eviction_plan.models_to_evict:
        return True

    models_to_evict = eviction_plan.models_to_evict
    eviction_key = (
        gateway_name,
        tuple(
            sorted(
                (
                    model_id.routing_key
                    if hasattr(model_id, "routing_key")
                    else str(model_id)
                )
                for model_id in models_to_evict
            )
        ),
    )
    existing = _inflight_evictions.get(eviction_key)
    if existing and not existing.done():
        logger.info(
            "🔁 Joining in-flight eviction on %s for %s",
            gateway_name,
            list(eviction_key[1]),
        )
        return await existing

    async def _run_eviction() -> bool:
        logger.info(
            f"🗑️ Executing eviction plan on {gateway_name}: "
            f"unloading {len(models_to_evict)} model(s): {list(models_to_evict)}"
        )

        # Create event waiter for MODEL_UNLOADED events (unified local + federated)
        from .event_waiter import EvictionWaiter, UnloadResult

        waiter = EvictionWaiter(event_bus) if event_bus else None
        if waiter:
            await waiter.start()

        try:
            # Execute unload for each model sequentially
            for model_id in models_to_evict:
                # Use parent request_id with suffix for tracing
                model_id_str = str(model_id)
                unload_request_id = (
                    f"{request_id}-evict-{model_id_str[:8]}"
                    if request_id
                    else str(uuid.uuid4())
                )

                try:
                    # Step 1: Register wait handle BEFORE HTTP request (prevents race)
                    # Event may arrive before wait_for_registered() is called
                    if waiter:
                        waiter.register_wait(gateway_name, model_id)

                    # Step 2: Send HTTP unload request
                    result = await forwarder.forward_model_unload_request(
                        gateway=federated_gateway,
                        model_id=model_id,
                        request_id=unload_request_id,
                    )

                    # Check HTTP response status
                    if result.get("status") != "ok":
                        logger.error(
                            f"❌ Eviction HTTP request failed for {model_id_str} "
                            f"on {gateway_name}: status={result.get('status')}, "
                            f"message={result.get('message')}"
                        )
                        return False

                    logger.debug(
                        f"✅ Unload HTTP request succeeded for {model_id_str}, "
                        f"waiting for MODEL_UNLOADED event..."
                    )

                    # Step 3: Wait for MODEL_UNLOADED event (resources freed)
                    if waiter:
                        unload_result = await waiter.wait_for_registered(
                            gateway_name=gateway_name,
                            model_id=model_id,
                            timeout=10.0,  # Force unload timeout
                        )

                        if unload_result != UnloadResult.UNLOADED:
                            logger.error(
                                "❌ MODEL_UNLOADED event not received for "
                                f"{model_id_str} "
                                f"on {gateway_name}: {unload_result.value}"
                            )
                            return False

                        logger.info(
                            f"✅ Evicted {model_id_str} from {gateway_name} "
                            f"(event confirmed)"
                        )

                        if event_bus and eviction_plan:
                            from src.scheduling.events.model_lifecycle import (
                                WorkerEvicted,
                            )

                            trigger = eviction_plan.trigger_model_id or "unknown"
                            await event_bus.publish_async_nowait(
                                WorkerEvicted(
                                    model_id=model_id_str,
                                    trigger_model_id=trigger,
                                    vram_freed_mb=eviction_plan.freed_vram_mb,
                                    gateway_name=gateway_name,
                                )
                            )
                    else:
                        # No event_bus - fall back to assuming success
                        # (less reliable but maintains backward compatibility)
                        logger.warning(
                            f"No event_bus available, assuming {model_id_str} "
                            f"unload succeeded (no event confirmation)"
                        )

                except Exception as e:
                    logger.error(
                        f"❌ Failed to evict {model_id_str} from {gateway_name}: {e}"
                    )
                    return False

            logger.info(
                f"✅ Eviction complete on {gateway_name}: "
                f"freed ~{eviction_plan.freed_ram_mb}MB RAM, "
                f"~{eviction_plan.freed_vram_mb}MB VRAM (event-driven confirmation)"
            )
            return True

        finally:
            if waiter:
                waiter.stop()

    task = asyncio.create_task(
        _run_eviction(),
        name=f"evict:{gateway_name}:{','.join(eviction_key[1])}",
    )
    _inflight_evictions[eviction_key] = task
    try:
        return await task
    finally:
        if _inflight_evictions.get(eviction_key) is task:
            _inflight_evictions.pop(eviction_key, None)
