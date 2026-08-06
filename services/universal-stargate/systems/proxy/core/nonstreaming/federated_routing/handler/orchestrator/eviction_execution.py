"""Master-mode eviction execution with cooldown-class policy and terminal results."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.routing.selection.decision.admission_verdict import AdmissionVerdict
from systems.routing.selection.decision.eviction_cooldown_policy import (
    COOLDOWN_OVERRIDE_OSCILLATION_WINDOW_S,
    CooldownOverrideKey,
    oscillation_blocks_override,
    record_cooldown_override,
)

if TYPE_CHECKING:
    from model_id import ModelId
    from universal_event_bus import EventBus

    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.selection.decision.types import DecisionTrace
    from systems.routing.selection.types import Gateway

logger = get_logger(__name__)


class MasterEvictionOutcome(StrEnum):
    EVICTED = "evicted"
    BLOCKED = "blocked"
    EXECUTION_FAILED = "execution_failed"
    NO_EVICTION_NEEDED = "no_eviction_needed"


@dataclass(frozen=True, kw_only=True)
class MasterEvictionResult:
    """Terminal eviction outcome consumed by the evict-before-load sequencing gate."""

    outcome: MasterEvictionOutcome
    reason: str | None = None
    retry_after_s: float | None = None
    verdict_class: str | None = None
    gateway_id: str | None = None
    victim_model_id: str | None = None
    requester: str | None = None

    @property
    def permits_load(self) -> bool:
        return self.outcome in {
            MasterEvictionOutcome.EVICTED,
            MasterEvictionOutcome.NO_EVICTION_NEEDED,
        }


def _blocked_result(
    *,
    reason: str,
    gateway_id: str,
    retry_after_s: float | None = None,
    victim_model_id: str | None = None,
    requester: str | None = None,
) -> MasterEvictionResult:
    return MasterEvictionResult(
        outcome=MasterEvictionOutcome.BLOCKED,
        reason=reason,
        retry_after_s=retry_after_s,
        verdict_class=AdmissionVerdict.INSUFFICIENT_TRANSIENT.value,
        gateway_id=gateway_id,
        victim_model_id=victim_model_id,
        requester=requester,
    )


async def _emit_cooldown_override_event(
    *,
    event_bus: EventBus,
    model_id: str,
    gateway_id: str,
    node_id: str,
    remaining_s: float,
    requester: str | None,
) -> None:
    from src.scheduling.events.routing import EvictionCooldownOverridden

    await event_bus.publish_nowait(
        EvictionCooldownOverridden(
            model=model_id,
            node=node_id or gateway_id,
            remaining_s=remaining_s,
            requester=requester or "",
            gateway_id=gateway_id,
            timestamp=time.time(),
        )
    )


async def execute_master_eviction(
    *,
    federation_forwarder: FederatedRequestForwarder | None,
    federated_manager: FederatedGatewayManager | None,
    selected_gateway: Gateway,
    trace: DecisionTrace,
    request_id: str | None,
    event_bus: EventBus | None = None,
    eviction_cooldown_s: float = 120.0,
) -> MasterEvictionResult:
    """Execute eviction and return a terminal outcome for the load sequencing gate."""
    from systems.federation.common.types import FederatedGateway
    from systems.routing.eviction.executor import (
        execute_eviction_plan,
        get_eviction_plan_for_gateway,
    )

    requester = request_id or ""
    gateway_name = selected_gateway.name
    node_id = selected_gateway.node_id or gateway_name
    eviction_plan = get_eviction_plan_for_gateway(trace, gateway_name)

    from systems.routing.selection.decision.types import is_eviction_plan_actionable

    if not is_eviction_plan_actionable(eviction_plan):
        return MasterEvictionResult(
            outcome=MasterEvictionOutcome.NO_EVICTION_NEEDED,
            gateway_id=gateway_name,
            requester=requester,
        )

    if not isinstance(selected_gateway.ref, FederatedGateway):
        logger.warning(
            "Cannot execute eviction: gateway ref is not FederatedGateway "
            "(got %s)",
            type(selected_gateway.ref).__name__,
        )
        return _blocked_result(
            reason="invalid_gateway_ref",
            gateway_id=gateway_name,
            requester=requester,
        )

    if not federation_forwarder:
        logger.error(
            "Cannot execute eviction: no federation_forwarder configured "
            "(Master mode required)"
        )
        return _blocked_result(
            reason="missing_federation_forwarder",
            gateway_id=gateway_name,
            requester=requester,
        )

    if eviction_plan.cooldown_override_pending:
        victim_id = eviction_plan.cooldown_override_victim_id or ""
        remaining_s = float(eviction_plan.cooldown_override_remaining_s or 0.0)
        override_key = CooldownOverrideKey(
            gateway_id=gateway_name,
            victim_model_id=victim_id,
        )
        if oscillation_blocks_override(
            override_key,
            window_s=COOLDOWN_OVERRIDE_OSCILLATION_WINDOW_S,
        ):
            retry_after = max(1.0, remaining_s)
            logger.warning(
                "Cooldown oscillation breaker: honoring cooldown for %s on %s "
                "(retry_after_s=%.1fs)",
                victim_id,
                gateway_name,
                retry_after,
            )
            return _blocked_result(
                reason="cooldown_oscillation_breaker",
                gateway_id=gateway_name,
                retry_after_s=retry_after,
                victim_model_id=victim_id,
                requester=requester,
            )

        if event_bus is not None:
            await _emit_cooldown_override_event(
                event_bus=event_bus,
                model_id=victim_id,
                gateway_id=gateway_name,
                node_id=node_id,
                remaining_s=remaining_s,
                requester=requester,
            )
        record_cooldown_override(override_key)

    marked_transitioning: list[ModelId] = []
    if federated_manager is not None:
        for model_id in eviction_plan.models_to_evict:
            if federated_manager.mark_loading_optimistic(
                selected_gateway.ref.gateway_id, model_id
            ):
                marked_transitioning.append(model_id)

    ok = False
    try:
        outcome = await execute_eviction_plan(
            forwarder=federation_forwarder,
            federated_gateway=selected_gateway.ref,
            eviction_plan=eviction_plan,
            gateway_name=gateway_name,
            request_id=request_id,
            event_bus=event_bus,
        )
        ok = outcome.ok
    finally:
        if federated_manager is not None and marked_transitioning and not ok:
            for model_id in marked_transitioning:
                try:
                    await federated_manager.clear_model_loading(
                        selected_gateway.ref.gateway_id, model_id
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to clear transitioning mark for %s on %s: %s",
                        model_id,
                        selected_gateway.ref.gateway_id,
                        exc,
                    )

    if ok:
        return MasterEvictionResult(
            outcome=MasterEvictionOutcome.EVICTED,
            gateway_id=gateway_name,
            requester=requester,
        )

    return MasterEvictionResult(
        outcome=MasterEvictionOutcome.EXECUTION_FAILED,
        reason="eviction_execution_failed",
        gateway_id=gateway_name,
        requester=requester,
    )


def result_to_error_data(result: MasterEvictionResult) -> dict[str, Any]:
    """Structured failure payload for waiter/client propagation."""
    data: dict[str, Any] = {
        "gateway": result.gateway_id,
        "reason": result.reason,
        "requester": result.requester,
    }
    if result.victim_model_id:
        data["victim_model_id"] = result.victim_model_id
    if result.verdict_class:
        data["verdict_class"] = result.verdict_class
    if result.retry_after_s is not None:
        data["retry_after_s"] = result.retry_after_s
    return data
