"""
OOM recovery for nonstreaming inference requests.

When inference returns 500 (likely VRAM exhaustion), this module evicts all
idle co-loaded models from the gateway to give the target model exclusive
GPU access. The caller retries inference after a successful eviction.

Architecture:
    detect 500 → attempt_oom_recovery() → {evict idle models} → return ok
    Caller: retry if ok, ban if retry also fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from model_id import ModelId
    from universal_event_bus import EventBus

    from systems.federation.common.types import FederatedGateway
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.federation.master.routing.orchestrator import MasterRequestTracker

logger = get_logger(__name__)


async def attempt_oom_recovery(
    *,
    gateway: FederatedGateway,
    model_id: ModelId,
    federated_manager: FederatedGatewayManager,
    federation_forwarder: FederatedRequestForwarder,
    request_tracker: MasterRequestTracker | None,
    event_bus: EventBus | None,
    request_id: str,
) -> bool:
    """Evict all idle models from a gateway to free VRAM for the failed model.

    "Clear the room": unload every loaded model on the gateway EXCEPT:
    - The target model (the one that OOM'd — we want to retry it)
    - Models with in-flight routing keys (busy with other requests)

    Returns True if at least one model was evicted (retry may succeed).
    Returns False if nothing could be evicted (retry pointless — the model
    already had exclusive GPU access and still failed).

    INVARIANT: ∀ evicted_model: ¬has_routing_key(evicted_model)
    INVARIANT: target_model ∉ evicted_models
    """
    from systems.routing.eviction.executor import execute_eviction_plan
    from systems.routing.selection.decision.types import EvictionPlanSummary

    gateway_id = gateway.gateway_id

    # Determine which models are busy (protected from eviction)
    busy_routing_keys: set[str] = set()
    if request_tracker is not None:
        busy_routing_keys = request_tracker.get_routing_keys_in_flight(gateway_id)

    # Build eviction candidates: loaded models minus target and busy
    target_routing_key = model_id.routing_key
    idle_models = frozenset(
        m
        for m in gateway.loaded_models
        if m.routing_key != target_routing_key
        and m.routing_key not in busy_routing_keys
    )

    if not idle_models:
        logger.warning(
            "🔒 OOM recovery: no idle models to evict on %s "
            "(loaded=%d, busy_keys=%d, target=%s)",
            gateway_id,
            len(gateway.loaded_models),
            len(busy_routing_keys),
            model_id,
        )
        return False

    logger.warning(
        "🧹 OOM recovery: evicting %d idle model(s) from %s to free VRAM "
        "for %s (request=%s)",
        len(idle_models),
        gateway_id,
        model_id,
        request_id[:8],
    )

    if event_bus:
        _emit_recovery_started(
            event_bus,
            request_id=request_id,
            model_id=model_id.routing_key,
            gateway_id=gateway_id,
            evicting_count=len(idle_models),
            evicting_models=[str(m) for m in idle_models],
        )

    plan = EvictionPlanSummary(
        models_to_evict=idle_models,
        freed_vram_mb=0,
        freed_ram_mb=0,
        estimated_cost=0.0,
    )

    ok = await execute_eviction_plan(
        forwarder=federation_forwarder,
        federated_gateway=gateway,
        eviction_plan=plan,
        gateway_name=gateway_id,
        request_id=request_id,
        event_bus=event_bus,
    )

    if not ok:
        logger.error(
            "❌ OOM recovery: eviction failed on %s (request=%s)",
            gateway_id,
            request_id[:8],
        )

    return ok


def _emit_recovery_started(
    event_bus: EventBus,
    *,
    request_id: str,
    model_id: str,
    gateway_id: str,
    evicting_count: int,
    evicting_models: list[str],
) -> None:
    """Fire-and-forget recovery started event."""
    from src.scheduling.events.routing import OomRecoveryStarted

    event_bus.publish_async_nowait(
        OomRecoveryStarted(
            request_id=request_id,
            model_id=model_id,
            gateway_id=gateway_id,
            evicting_count=evicting_count,
            evicting_models=evicting_models,
        )
    )
