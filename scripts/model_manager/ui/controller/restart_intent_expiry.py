"""Restart-intent arm clock.

Past ``expires_at``, a live ``pending_drain`` arm (non wait-for-boundary without
a window, or caller TTL) terminalizes as ``expired``, emits
``manage.restart.expired``, and releases the restart gate via supervise exit.
Lazy expiry on re-arm covers rows the sweep has not yet visited.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from universal_logging import get_logger

from .restart_intent_migrate import INTENT_EXPIRY_WINDOW_S
from .restart_intent_reason_codes import TTL_EXPIRED
from .restart_intent_states import STATUS_EXPIRED, STATUS_PENDING_DRAIN

logger = get_logger(__name__)

ReleaseDrain = Callable[[str, int], Awaitable[Any]]


def resolve_intent_ttl_s(
    *, wait_for_boundary: bool, intent_ttl_s: float | None
) -> float | None:
    """Default 600s arm; wait_for_boundary defaults to no expiry; caller TTL wins."""
    if intent_ttl_s is not None:
        return float(intent_ttl_s)
    if wait_for_boundary:
        return None
    return INTENT_EXPIRY_WINDOW_S


def join_expires_at(
    *,
    wait_for_boundary: bool,
    intent_ttl_s: float | None,
    prior_expires_at: str | None,
    now: datetime,
) -> str | None:
    """Join/rearm expiry: explicit TTL refreshes; wait_for_boundary keeps prior.

    A joining call that omits ``intent_ttl_s`` must not wipe a caller-set
    ``expires_at`` on a wait_for_boundary arm (wrong-ordering join).
    """
    if intent_ttl_s is not None:
        _agent, _armed, expires = arm_stamps("join", now=now, ttl_s=float(intent_ttl_s))
        return expires
    if wait_for_boundary:
        return prior_expires_at
    _agent, _armed, expires = arm_stamps("join", now=now, ttl_s=INTENT_EXPIRY_WINDOW_S)
    return expires


def arm_stamps(
    caller_agent: str | None,
    *,
    now: datetime,
    ttl_s: float | None = INTENT_EXPIRY_WINDOW_S,
) -> tuple[str, str, str | None]:
    """``(caller_agent, armed_at, expires_at)``. ``ttl_s=None`` → no expiry."""
    agent = (caller_agent or "").strip() or "unknown"
    armed = now.astimezone(UTC).isoformat()
    if ttl_s is None:
        return agent, armed, None
    expires = (now.astimezone(UTC) + timedelta(seconds=ttl_s)).isoformat()
    return agent, armed, expires


def intent_expired(intent: Any, *, now: datetime | None = None) -> bool:
    """True when a pending arm is past ``expires_at``. Missing window is not due."""
    if getattr(intent, "status", None) != STATUS_PENDING_DRAIN:
        return False
    if getattr(intent, "wait_for_boundary", False) and not getattr(
        intent, "expires_at", None
    ):
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


async def giw_cancel_drain(intent_id: str, drain_epoch: int) -> dict[str, Any]:
    """POST worker admin cancel-drain for the matching intent generation."""
    from transport_utils import make_async_client

    from .restart_drain import GIT_INTEGRATION_WORKER_URL

    async with make_async_client(GIT_INTEGRATION_WORKER_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/git/admin/cancel-drain",
            json={"intent_id": intent_id, "drain_epoch": drain_epoch},
        )
        resp.raise_for_status()
        return resp.json()


def release_drain_for_intent(intent: Any) -> ReleaseDrain | None:
    if intent.drain_epoch is None:
        return None
    if getattr(intent, "service", None) == "git_integration_worker":
        return giw_cancel_drain
    return None


async def expire_pending_intent(
    store: Any,
    intent_id: str,
    *,
    release_drain: ReleaseDrain | None = None,
    now: datetime | None = None,
    emit_event: bool = True,
) -> bool:
    """Terminalize a due ``pending_drain`` arm as ``expired``. Returns True on success."""
    intent = store.get(intent_id)
    if intent is None or not intent_expired(intent, now=now):
        return False
    if release_drain is None:
        release_drain = release_drain_for_intent(intent)
    if intent.drain_epoch is not None:
        if release_drain is None:
            logger.warning(
                "restart intent expiry skipped; drain release unavailable intent_id=%s",
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
    if (
        store.advance_if_status(
            intent_id,
            from_status=STATUS_PENDING_DRAIN,
            to_status=STATUS_EXPIRED,
            reason=TTL_EXPIRED,
        )
        != 1
    ):
        return False
    refreshed = store.get(intent_id)
    last_reason = (
        (refreshed.status_reason or refreshed.reason or "")
        if refreshed is not None
        else ""
    )
    armed_at = intent.armed_at
    expires_at = intent.expires_at
    logger.warning(
        "restart intent expired intent_id=%s service=%s caller_agent=%s",
        intent_id,
        intent.service,
        getattr(intent, "caller_agent", None) or "unknown",
    )
    if emit_event:
        from scripts.model_manager.observation_event import emit_manage_restart_expired

        await emit_manage_restart_expired(
            intent_id=intent_id,
            service=intent.service,
            armed_at=armed_at,
            expires_at=expires_at,
            last_status_reason=last_reason,
        )
    return True


async def expire_via_cancel(
    store: Any,
    intent_id: str,
    *,
    release_drain: Callable[[str, int], Awaitable[Any]] | None = None,
    now: datetime | None = None,
) -> bool:
    """Compatibility alias — expiry terminalizes as ``expired``, not ``cancelled``."""
    return await expire_pending_intent(
        store, intent_id, release_drain=release_drain, now=now
    )


async def clear_expired_arm_before_rearm(
    store: Any,
    service: str,
    *,
    release_drain: ReleaseDrain | None = None,
    now: datetime | None = None,
) -> bool:
    """Lazy expiry so re-arm can mint a fresh intent after the gate reopens."""
    from .drain_timeout_keep_await import cancel_or_join_supervise_task

    intent = store.active_for_service(service)
    if intent is None or not intent_expired(intent, now=now):
        return False
    resolved_release = release_drain or release_drain_for_intent(intent)
    if not await expire_pending_intent(
        store,
        intent.intent_id,
        release_drain=resolved_release,
        now=now,
    ):
        return False
    await cancel_or_join_supervise_task(service)
    return True


async def sweep_expired_pending_intents(
    store: Any,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Expire every due ``pending_drain`` row and join its supervise task."""
    from .drain_timeout_keep_await import cancel_or_join_supervise_task

    clock = now or datetime.now(UTC)
    expired_ids: list[str] = []
    for intent in store.pending_intents():
        if intent.status != STATUS_PENDING_DRAIN:
            continue
        if not intent_expired(intent, now=clock):
            continue
        if await expire_pending_intent(
            store,
            intent.intent_id,
            release_drain=release_drain_for_intent(intent),
            now=clock,
        ):
            expired_ids.append(intent.intent_id)
            await cancel_or_join_supervise_task(intent.service)
    return expired_ids
