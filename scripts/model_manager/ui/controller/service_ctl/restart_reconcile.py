"""Boot-time restart-intent resume helpers for ServiceController."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from charter_runner_store.propagation_validation import (
    reconcile_pending_validations_at_boot,
)

from ..git_worker_activation_verify import resume_activation_verify
from ..restart_drain import resume_drain_supervision
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
            supervisor = controller.build_git_worker_drain_supervisor(
                kill=controller.git_worker_kill_for(intent.action)
            )
            await resume_drain_supervision(
                controller._restart_gate,
                intent.service,
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
                store.advance(intent.intent_id, status="failed")
            except Exception:
                logger.exception(
                    "restart-intent reconcile: cannot mark intent failed: %s",
                    intent.intent_id,
                )
    reconcile_pending_validations_at_boot(store=store, logger=logger)


__all__ = ["reconcile_pending_restart_intents"]
