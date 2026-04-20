"""
Centralized model.execution.completed emission.

Single source of truth for request-scoped completion events.
All terminal request outcomes must emit via this module.

INV: ∀ acquired_slot: emit_execution_completed() called exactly once

Usage Note:
    Callers use function-level imports:
        from systems.proxy.core.lifecycle import emit_execution_completed

    This is imported at function call sites (not top-level) to keep
    imports localized. No circular dependency risk - this pattern is
    acceptable but could be moved to top-level imports if preferred.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


async def emit_execution_completed(
    event_bus: EventBus | None,
    *,
    url: str,
    model_id: str,
    request_id: str,
    gateway_id: str,
) -> None:
    """
    Emit model.execution.completed event (central emission point).

    MUST be called exactly once per acquired slot on SUCCESSFUL terminal outcomes.
    For failures, use emit_execution_failed().

    Args:
        event_bus: Event bus (if None, logs warning and returns)
        url: Gateway URL
        model_id: Model identifier
        request_id: Request identifier (for slot tracking)
        gateway_id: Gateway identifier (for slot tracking)

    INV: Logs ERROR on failure (no silent failures)
    """
    if event_bus is None:
        logger.warning(
            "⚠️ Cannot emit model.execution.completed: event_bus is None "
            "(request=%s, gateway=%s)",
            request_id[:8],
            gateway_id,
        )
        return

    try:
        from src.scheduling.events import ModelExecutionCompleted

        await event_bus.publish_nowait(
            ModelExecutionCompleted(
                url=url,
                model_id=model_id,
                request_id=request_id,
                gateway_id=gateway_id,
            )
        )
        logger.debug(
            "🔔 Emitted model.execution.completed: request=%s gateway=%s model=%s",
            request_id[:8],
            gateway_id,
            model_id,
        )
    except Exception as exc:
        logger.error(
            "❌ Failed to emit model.execution.completed: %s "
            "(request=%s, gateway=%s) - slot may leak!",
            exc,
            request_id[:8],
            gateway_id,
        )


async def emit_execution_failed(
    event_bus: EventBus | None,
    *,
    url: str,
    model_id: str,
    request_id: str,
    gateway_id: str,
    error: str,
) -> None:
    """
    Emit model.execution.failed event (central emission point).

    MUST be called exactly once per acquired slot on FAILED terminal outcomes.

    Args:
        event_bus: Event bus (if None, logs warning and returns)
        url: Gateway URL
        model_id: Model identifier
        request_id: Request identifier (for slot tracking)
        gateway_id: Gateway identifier (for slot tracking)
        error: Error message

    INV: Logs ERROR on failure (no silent failures)
    """
    if event_bus is None:
        logger.warning(
            "⚠️ Cannot emit model.execution.failed: event_bus is None "
            "(request=%s, gateway=%s)",
            request_id[:8],
            gateway_id,
        )
        return

    try:
        from src.scheduling.events import ModelExecutionFailed

        await event_bus.publish_nowait(
            ModelExecutionFailed(
                url=url,
                model_id=model_id,
                request_id=request_id,
                gateway_id=gateway_id,
                error=error,
            )
        )
        logger.debug(
            "🔔 Emitted model.execution.failed: request=%s gateway=%s model=%s error=%s",
            request_id[:8],
            gateway_id,
            model_id,
            error,
        )
    except Exception as exc:
        logger.error(
            "❌ Failed to emit model.execution.failed: %s "
            "(request=%s, gateway=%s) - slot may leak!",
            exc,
            request_id[:8],
            gateway_id,
        )
