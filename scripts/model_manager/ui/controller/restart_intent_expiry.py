"""Restart-intent arm clock.

Expiry is a cancel: release the drain, then ``store.cancel``. Read-only
checks and schema migration do not write ``timeout``. A re-arm is a joining
``create_intent`` or ``RestartIntentStore.rearm``, not the supervisor's 30s
progress tick.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from universal_logging import get_logger

from .restart_intent_migrate import INTENT_EXPIRY_WINDOW_S
from .restart_intent_states import STATUS_PENDING_DRAIN

logger = get_logger(__name__)


def arm_stamps(caller_agent: str | None, *, now: datetime) -> tuple[str, str, str]:
    """``(caller_agent, armed_at, expires_at)``. Blank caller is ``unknown``."""
    agent = (caller_agent or "").strip() or "unknown"
    armed = now.astimezone(UTC).isoformat()
    expires = (
        now.astimezone(UTC) + timedelta(seconds=INTENT_EXPIRY_WINDOW_S)
    ).isoformat()
    return agent, armed, expires


def intent_expired(intent: Any, *, now: datetime | None = None) -> bool:
    """True when a pending arm is past ``expires_at``. Missing window is not due."""
    if getattr(intent, "status", None) != STATUS_PENDING_DRAIN:
        return False
    expires_at = getattr(intent, "expires_at", None)
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    clock = now or datetime.now(UTC)
    return expires <= clock


def intent_defers(intent: Any, *, now: datetime | None = None) -> bool:
    """True while a pending arm is still inside its window. Does not write."""
    if getattr(intent, "status", None) != STATUS_PENDING_DRAIN:
        return False
    return not intent_expired(intent, now=now)


def apply_intent_to_busy_entry(
    entry: dict[str, Any],
    intent: Any,
    *,
    now: datetime | None = None,
) -> None:
    """Project expiry onto a busy_status dict. Does not write the store."""
    from .restart_intent_consumer import project_restart_intent_consumer

    clock = now or datetime.now(UTC)
    if intent_defers(intent, now=clock):
        projected = project_restart_intent_consumer(intent, now=clock)
        projected["caller_agent"] = getattr(intent, "caller_agent", None)
        projected["armed_at"] = getattr(intent, "armed_at", None)
        projected["expires_at"] = getattr(intent, "expires_at", None)
        entry["restart_would_defer"] = True
        entry["restart_intent"] = projected
        return
    entry["restart_intent"] = None
    if not entry.get("busy"):
        entry["restart_would_defer"] = False


async def expire_via_cancel(
    store: Any,
    intent_id: str,
    *,
    release_drain: Callable[[str, int], Awaitable[Any]] | None = None,
    now: datetime | None = None,
) -> bool:
    """Release the drain, then cancel. Returns True when the row was cancelled.

    Does not set ``status=timeout``. If release fails, the row stays pending
    so a later cancel can still release. Not for read-only or boot paths.
    """
    intent = store.get(intent_id)
    if intent is None or not intent_expired(intent, now=now):
        return False
    if intent.drain_epoch is not None:
        if release_drain is None:
            logger.warning(
                "restart intent expiry skipped; drain release unavailable "
                "intent_id=%s",
                intent_id,
            )
            return False
        try:
            await release_drain(intent.intent_id, int(intent.drain_epoch))
        except Exception:
            logger.warning(
                "restart intent expiry release failed; leaving pending_drain "
                "intent_id=%s",
                intent_id,
                exc_info=True,
            )
            return False
    store.cancel(intent_id)
    logger.warning(
        "restart intent expired via cancel intent_id=%s caller_agent=%s",
        intent_id,
        getattr(intent, "caller_agent", None) or "unknown",
    )
    return True
