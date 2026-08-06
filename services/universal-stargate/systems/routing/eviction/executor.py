"""
Eviction execution for federated gateways (HTTP unload + event confirmation).

Shared by ModelRouter and router-only paths. When EventBus is absent, eviction
does not assume success — callers receive UNCONFIRMED_NO_BUS (.ok is False).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.federation.common.types import FederatedGateway
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.selection.decision.types import (
        EvictionPlanAbort,
        EvictionPlanSummary,
    )
    from systems.routing.selection.types import DecisionTrace

logger = get_logger(__name__)


class EvictionStatus(Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED_NO_BUS = "unconfirmed_no_bus"
    FAILED_HTTP = "failed_http"
    FAILED_TIMEOUT = "failed_timeout"
    ABORTED_SHUTDOWN = "aborted_shutdown"


@dataclass(frozen=True)
class EvictionOutcome:
    """Typed result of execute_eviction_plan."""

    status: EvictionStatus
    failed_model_id: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == EvictionStatus.CONFIRMED


class EvictionInflightRegistry:
    """Executor-owned inflight eviction tasks keyed by (gateway, model routing key)."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task[EvictionOutcome]] = {}

    def get_inflight(
        self, gateway_name: str, model_key: str
    ) -> asyncio.Task[EvictionOutcome] | None:
        task = self._tasks.get((gateway_name, model_key))
        if task is not None and not task.done():
            return task
        return None

    def register(
        self,
        gateway_name: str,
        model_key: str,
        task: asyncio.Task[EvictionOutcome],
    ) -> None:
        self._tasks[(gateway_name, model_key)] = task

    def release(
        self,
        gateway_name: str,
        model_key: str,
        task: asyncio.Task[EvictionOutcome],
    ) -> None:
        key = (gateway_name, model_key)
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)


_inflight_registry = EvictionInflightRegistry()


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


def _model_routing_key(model_id: ModelId) -> str:
    return model_id.routing_key if hasattr(model_id, "routing_key") else str(model_id)


async def execute_eviction_plan(
    forwarder: FederatedRequestForwarder,
    federated_gateway: FederatedGateway,
    eviction_plan: EvictionPlanSummary | EvictionPlanAbort | None,
    gateway_name: str,
    request_id: str | None = None,
    event_bus=None,
    *,
    inflight_registry: EvictionInflightRegistry | None = None,
) -> EvictionOutcome:
    """
    Execute eviction plan by sending unload commands to Remote.

    INVARIANT: unload confirmation is required when EventBus + waiter are present.
    No EventBus means UNCONFIRMED_NO_BUS (fail closed — not success).

    Flow per model:
    1. Register wait handle (BEFORE HTTP to prevent race)
    2. Send HTTP unload request
    3. Wait for MODEL_UNLOADED event from EventBus
    4. Return CONFIRMED only when the event confirms resources freed

    Args:
        forwarder: Request forwarder for sending unload commands
        federated_gateway: Target gateway (FederatedGateway ref)
        eviction_plan: Plan with models to evict (None = no eviction)
        gateway_name: Gateway name for logging
        request_id: Parent request ID for tracing
        event_bus: EventBus for subscribing to MODEL_UNLOADED events
        inflight_registry: Optional registry override (tests / DI)

    Returns:
        EvictionOutcome with .ok True only on CONFIRMED status
    """
    from systems.routing.selection.decision.types import is_eviction_plan_actionable

    if not is_eviction_plan_actionable(eviction_plan):
        return EvictionOutcome(status=EvictionStatus.CONFIRMED)

    registry = inflight_registry or _inflight_registry

    for model_id in eviction_plan.models_to_evict:
        model_key = _model_routing_key(model_id)
        existing = registry.get_inflight(gateway_name, model_key)
        if existing is not None:
            logger.info(
                "🔁 Joining in-flight eviction for %s on %s",
                model_key,
                gateway_name,
            )
            joined = await existing
            if not joined.ok:
                return joined
            continue

        task = asyncio.create_task(
            _evict_single_model(
                forwarder=forwarder,
                federated_gateway=federated_gateway,
                model_id=model_id,
                gateway_name=gateway_name,
                request_id=request_id,
                event_bus=event_bus,
                eviction_plan=eviction_plan,
            ),
            name=f"evict:{gateway_name}:{model_key}",
        )
        registry.register(gateway_name, model_key, task)
        try:
            outcome = await task
        finally:
            registry.release(gateway_name, model_key, task)
        if not outcome.ok:
            return outcome

    logger.info(
        f"✅ Eviction complete on {gateway_name}: "
        f"freed ~{eviction_plan.freed_ram_mb}MB RAM, "
        f"~{eviction_plan.freed_vram_mb}MB VRAM"
    )
    return EvictionOutcome(status=EvictionStatus.CONFIRMED)


async def _evict_single_model(
    *,
    forwarder: FederatedRequestForwarder,
    federated_gateway: FederatedGateway,
    model_id: ModelId,
    gateway_name: str,
    request_id: str | None,
    event_bus,
    eviction_plan: EvictionPlanSummary,
) -> EvictionOutcome:
    from .event_waiter import EvictionWaiter, UnloadResult

    model_id_str = str(model_id)
    logger.info(
        f"🗑️ Evicting {model_id_str} on {gateway_name} "
        f"(plan size={len(eviction_plan.models_to_evict)})"
    )

    waiter = EvictionWaiter(event_bus) if event_bus else None
    if waiter:
        await waiter.start()

    unload_request_id = (
        f"{request_id}-evict-{model_id_str[:8]}"
        if request_id
        else str(uuid.uuid4())
    )

    try:
        if waiter:
            waiter.register_wait(gateway_name, model_id)

        result = await forwarder.forward_model_unload_request(
            gateway=federated_gateway,
            model_id=model_id,
            request_id=unload_request_id,
        )

        if result.get("status") != "ok":
            reason = result.get("message") or f"status={result.get('status')}"
            logger.error(
                f"❌ Eviction HTTP request failed for {model_id_str} "
                f"on {gateway_name}: {reason}"
            )
            return EvictionOutcome(
                status=EvictionStatus.FAILED_HTTP,
                failed_model_id=model_id_str,
                reason=reason,
            )

        if not waiter:
            logger.error(
                f"No event_bus available — cannot confirm unload of {model_id_str} "
                f"on {gateway_name} (fail closed)"
            )
            return EvictionOutcome(
                status=EvictionStatus.UNCONFIRMED_NO_BUS,
                failed_model_id=model_id_str,
                reason="no_event_bus_for_confirmation",
            )

        unload_result = await waiter.wait_for_registered(
            gateway_name=gateway_name,
            model_id=model_id,
            timeout=10.0,
        )

        if unload_result == UnloadResult.SHUTDOWN:
            return EvictionOutcome(
                status=EvictionStatus.ABORTED_SHUTDOWN,
                failed_model_id=model_id_str,
                reason="waiter_shutdown_before_confirmation",
            )

        if unload_result != UnloadResult.UNLOADED:
            logger.error(
                "❌ MODEL_UNLOADED event not received for "
                f"{model_id_str} on {gateway_name}: {unload_result.value}"
            )
            return EvictionOutcome(
                status=EvictionStatus.FAILED_TIMEOUT,
                failed_model_id=model_id_str,
                reason=unload_result.value,
            )

        logger.info(
            f"✅ Evicted {model_id_str} from {gateway_name} (event confirmed)"
        )

        from src.scheduling.events.model_lifecycle import WorkerEvicted

        trigger = eviction_plan.trigger_model_id or "unknown"
        await event_bus.publish_nowait(
            WorkerEvicted(
                model_id=model_id_str,
                trigger_model_id=trigger,
                vram_freed_mb=eviction_plan.freed_vram_mb,
                gateway_name=gateway_name,
            )
        )
        return EvictionOutcome(status=EvictionStatus.CONFIRMED)

    except Exception as exc:
        logger.error(
            f"❌ Failed to evict {model_id_str} from {gateway_name}: {exc}"
        )
        return EvictionOutcome(
            status=EvictionStatus.FAILED_HTTP,
            failed_model_id=model_id_str,
            reason=str(exc),
        )
    finally:
        if waiter:
            waiter.stop()
