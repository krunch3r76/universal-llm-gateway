"""Boot-time restart-intent resume helpers for ServiceController."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from charter_runner_store.propagation_validation import (
    reconcile_pending_validations_at_boot,
)

from ..drain_timeout_keep_await import repair_timeout_intent_gap
from ..git_worker_activation_verify import resume_activation_verify
from ..giw_recycle import recycle_deadline_s, recycle_idle_s
from ..restart_drain import resume_drain_supervision
from ..restart_intent_reason_codes import RECONCILE_RESUME_FAILED, reason_code
from ..restart_intent_states import (
    STATUS_DRAINED_RESTARTING,
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
)

if TYPE_CHECKING:
    from .core import ServiceController

logger = logging.getLogger(__name__)


async def reconcile_pending_restart_intents(controller: ServiceController) -> None:
    """Resume persisted restart intents and pending validations at manage boot."""
    store = controller._restart_intent_store
    try:
        repairs = repair_timeout_intent_gap(store)
        if repairs:
            logger.info("restart-intent reconcile: timeout gap repairs=%s", repairs)
    except Exception:
        logger.exception("restart-intent reconcile: timeout gap repair failed")
    try:
        pending = store.pending_intents()
    except Exception:
        logger.exception("restart-intent reconcile: cannot read pending intents")
        return
    for intent in pending:
        try:
            if intent.status == STATUS_VERIFYING_ACTIVATION:
                await resume_activation_verify(store, intent.intent_id)
                logger.info(
                    "restart-intent reconcile: resumed activation verify intent_id=%s",
                    intent.intent_id,
                )
                continue
            if intent.status not in (STATUS_PENDING_DRAIN, STATUS_DRAINED_RESTARTING):
                continue
            service = intent.service
            if service == "git_integration_worker":
                if intent.action == "recycle_giw":
                    supervisor = controller.build_git_worker_drain_supervisor(
                        kill=controller.git_worker_kill_for(intent.action),
                        action=intent.action,
                        idle_escalate_s=recycle_idle_s(),
                        deadline_s=recycle_deadline_s(),
                        park_first=True,
                    )
                else:
                    supervisor = controller.build_git_worker_drain_supervisor(
                        kill=controller.git_worker_kill_for(intent.action),
                        action=intent.action,
                    )
            elif service == "stargate":
                from scripts.model_manager.ui.api_dispatch import (
                    _lifecycle_with_restart_window,
                    _sync_restart,
                )

                from ..restart_drain import StargateIdleDrainSupervisor

                supervisor = StargateIdleDrainSupervisor(
                    gate=controller._restart_gate,
                    store=controller._restart_intent_store,
                    lifecycle=lambda: _lifecycle_with_restart_window(
                        controller,
                        "stargate",
                        intent.action,
                        lambda: _sync_restart(controller, "stargate"),
                    ),
                )
            elif service == "agent_bus":
                from scripts.model_manager.ui.api_dispatch import (
                    _lifecycle_with_restart_window,
                    _sync_restart,
                )

                from ..restart_drain import LocalServiceDrainSupervisor

                supervisor = LocalServiceDrainSupervisor(
                    gate=controller._restart_gate,
                    service="agent_bus",
                    store=controller._restart_intent_store,
                    lifecycle=lambda: _lifecycle_with_restart_window(
                        controller,
                        "agent_bus",
                        intent.action,
                        lambda: _sync_restart(controller, "agent_bus"),
                    ),
                )
            elif service == "mcp":
                from scripts.model_manager.ui.api_dispatch import _mcp_deferred_lifecycle

                from ..restart_drain import LocalServiceDrainSupervisor

                supervisor = LocalServiceDrainSupervisor(
                    gate=controller._restart_gate,
                    service="mcp",
                    store=controller._restart_intent_store,
                    lifecycle=lambda: _mcp_deferred_lifecycle(
                        controller, no_cache=False
                    ),
                )
            else:
                store.advance(
                    intent.intent_id,
                    status="failed",
                    reason=reason_code(
                        RECONCILE_RESUME_FAILED,
                        f"unsupported_service:{service}",
                    ),
                )
                continue
            await resume_drain_supervision(
                controller._restart_gate,
                service,
                supervisor=supervisor,
                intent=intent,
            )
            logger.info(
                "restart-intent reconcile: resumed intent_id=%s service=%s action=%s status=%s",
                intent.intent_id,
                intent.service,
                intent.action,
                intent.status,
            )
        except Exception:
            logger.exception(
                "restart-intent reconcile failed for intent_id=%s",
                intent.intent_id,
            )
            try:
                store.advance(
                    intent.intent_id,
                    status="failed",
                    reason=reason_code(RECONCILE_RESUME_FAILED),
                )
            except Exception:
                logger.exception(
                    "restart-intent reconcile: cannot mark intent failed: %s",
                    intent.intent_id,
                )
    reconcile_pending_validations_at_boot(store=store, logger=logger)


__all__ = ["reconcile_pending_restart_intents"]
