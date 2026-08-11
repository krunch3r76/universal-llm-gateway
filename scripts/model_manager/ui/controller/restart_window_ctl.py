"""Shared open/clear helpers for operator restart windows + observation emits."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from scripts.model_manager.observation_event import (
    emit_manage_restart_window_cleared,
    emit_manage_restart_window_opened,
)

from .propagation_settle_hook import invoke_propagation_settle_for_service
from .restart_window_store import (
    DEFAULT_FLEET_TTL_S,
    DEFAULT_SERVICE_TTL_S,
    FLEET_WINDOW_SERVICES,
    RETRY_AFTER_S,
    SCOPE_FLEET,
    SCOPE_SERVICE,
    RestartWindow,
)

if TYPE_CHECKING:
    from .restart_intent_store import RestartIntentStore


def _deadline_from_ttl(ttl_s: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat()


async def open_service_window(
    store: RestartIntentStore,
    service: str,
    *,
    reason: str,
    ttl_s: int = DEFAULT_SERVICE_TTL_S,
) -> RestartWindow:
    store.sweep_expired_windows()
    window = store.open_window(
        scope=SCOPE_SERVICE,
        service_set=[service],
        deadline_at=_deadline_from_ttl(ttl_s),
        reason=reason,
    )
    await emit_manage_restart_window_opened(
        window_id=window.window_id,
        scope=window.scope,
        service_set=window.service_set,
        deadline_at=window.deadline_at,
        reason=reason,
    )
    return window


async def open_fleet_window(
    store: RestartIntentStore,
    *,
    reason: str,
    service_set: list[str] | None = None,
    ttl_s: int = DEFAULT_FLEET_TTL_S,
) -> RestartWindow:
    store.sweep_expired_windows()
    services = service_set or list(FLEET_WINDOW_SERVICES)
    window = store.open_window(
        scope=SCOPE_FLEET,
        service_set=services,
        deadline_at=_deadline_from_ttl(ttl_s),
        reason=reason,
    )
    await emit_manage_restart_window_opened(
        window_id=window.window_id,
        scope=window.scope,
        service_set=window.service_set,
        deadline_at=window.deadline_at,
        reason=reason,
    )
    return window


async def clear_window(
    store: RestartIntentStore, window: RestartWindow, *, reason: str
) -> None:
    cleared = store.clear_window(window.window_id)
    if cleared is None:
        return
    await emit_manage_restart_window_cleared(
        window_id=cleared.window_id,
        scope=cleared.scope,
        service_set=cleared.service_set,
        reason=reason,
    )


async def clear_service_windows(
    store: RestartIntentStore, service: str, *, reason: str
) -> list[RestartWindow]:
    cleared = store.clear_open_for_service(service)
    for window in cleared:
        await emit_manage_restart_window_cleared(
            window_id=window.window_id,
            scope=window.scope,
            service_set=window.service_set,
            reason=reason,
        )
    return cleared


async def clear_fleet_windows(
    store: RestartIntentStore, *, reason: str
) -> list[RestartWindow]:
    cleared = store.clear_open_fleet_windows()
    for window in cleared:
        await emit_manage_restart_window_cleared(
            window_id=window.window_id,
            scope=window.scope,
            service_set=window.service_set,
            reason=reason,
        )
    return cleared


async def lifecycle_with_restart_window(
    store: RestartIntentStore,
    service: str,
    action: str,
    lifecycle: Any,
) -> str:
    """Open a window immediately before the first stop, clear after lifecycle."""
    await open_service_window(store, service, reason=f"manage {action}")
    clear_reason = "lifecycle failed"
    try:
        result = await lifecycle()
        clear_reason = "lifecycle completed"
        await invoke_propagation_settle_for_service(
            service,
            settle_not_before_monotonic=time.monotonic(),
            source="lifecycle_wrapper",
        )
        return result
    except asyncio.CancelledError:
        clear_reason = "lifecycle cancelled"
        raise
    except Exception:
        clear_reason = "lifecycle failed"
        raise
    finally:
        await clear_service_windows(store, service, reason=clear_reason)


def restart_window_annotation(
    store: RestartIntentStore, service: str
) -> dict[str, Any] | None:
    """Return restart_in_progress annotation fields when a window covers *service*."""
    window = store.window_for_service(service)
    if window is None:
        return None
    return {
        "restart_in_progress": True,
        "retry_after_s": RETRY_AFTER_S,
        "window_deadline": window.deadline_at,
        "window_id": window.window_id,
        "window_scope": window.scope,
    }
