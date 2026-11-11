"""Cancel target resolution and orchestration helpers for restart intents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from .restart_intent_expiry import giw_cancel_drain
from .restart_intent_states import (
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_PENDING_DRAIN,
)
from .restart_intent_store import RestartIntentCancelError, RestartIntentStore

logger = get_logger(__name__)

ReleaseDrain = Callable[[str, int], Awaitable[dict[str, Any]]]
ProbeDrainState = Callable[[], Awaitable[dict[str, Any]]]


def resolve_cancel_target(
    store: RestartIntentStore,
    *,
    intent_id: str,
    service: str | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Resolve an intent id, unique prefix, or live service row for cancel."""
    needle = (intent_id or "").strip()
    if needle:
        exact = store.get(needle)
        if exact is not None:
            return exact, None
        if len(needle) >= 8:
            matches = [
                row
                for row in store.pending_intents()
                if row.intent_id.startswith(needle)
            ]
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                return None, {
                    "status": "error",
                    "reason": "intent_id_ambiguous",
                    "intent_id": needle,
                    "candidates": [row.intent_id for row in matches],
                    "error": (
                        f"prefix {needle!r} matches {len(matches)} pending restart "
                        "intents; pass a longer id or the full intent_id"
                    ),
                }
        return None, {
            "status": "error",
            "reason": "intent_not_found",
            "intent_id": needle,
            "error": f"no restart intent matches id or prefix {needle!r}",
        }
    if service:
        live = store.active_for_service(service)
        if live is None:
            return None, {
                "status": "error",
                "reason": "no_live_intent",
                "service": service,
                "error": f"no live pending restart intent for service {service!r}",
            }
        return live, None
    return None, {
        "status": "error",
        "reason": "intent_not_found",
        "intent_id": needle,
        "error": "cancel_restart_intent requires intent_id or service",
    }


def worker_intent_released(state: dict[str, Any], intent_id: str) -> bool:
    """True when GIW drain-state shows this epoch is no longer armed/draining."""
    if not state:
        return False
    if "draining" not in state and "armed" not in state:
        return False
    draining = bool(state.get("draining"))
    armed = bool(state.get("armed"))
    if not draining and not armed:
        return True
    sid = state.get("intent_id")
    if sid and str(sid) != intent_id:
        return True
    return False


async def orchestrate_cancel_restart_intent(
    store: RestartIntentStore,
    *,
    intent_id: str,
    service: str | None = None,
    release_drain: ReleaseDrain | None = None,
    probe_drain_state: ProbeDrainState | None = None,
) -> dict[str, Any]:
    """Release-then-cancel with prefix/service resolution and drain-state recovery."""
    intent, error = resolve_cancel_target(store, intent_id=intent_id, service=service)
    if error is not None:
        return error
    assert intent is not None
    resolved_id = intent.intent_id
    if service is not None and intent.service != service:
        return {
            "status": "error",
            "reason": "service_mismatch",
            "intent_id": resolved_id,
            "service": intent.service,
            "requested_service": service,
            "error": (
                f"intent {resolved_id!r} is for service {intent.service!r}, "
                f"not {service!r}"
            ),
        }
    if intent.status == STATUS_EXPIRED:
        return {
            "status": "expired",
            "already_terminal": True,
            "intent_id": resolved_id,
            "service": intent.service,
        }
    if intent.status == STATUS_CANCELLED:
        return {
            "status": "cancelled",
            "intent_id": resolved_id,
            "service": intent.service,
            "already_cancelled": True,
            "drain_release": None,
        }
    if intent.status != STATUS_PENDING_DRAIN:
        return {
            "status": "refused",
            "reason": "cancel_refused_after_final_check_or_terminal",
            "intent_id": resolved_id,
            "intent_status": intent.status,
        }

    drain_release: dict[str, Any] | None = None
    if intent.drain_epoch is not None:
        release = release_drain or giw_cancel_drain
        try:
            drain_release = await release(resolved_id, int(intent.drain_epoch))
        except Exception as exc:
            if probe_drain_state is not None:
                try:
                    state = await probe_drain_state()
                except Exception as probe_exc:
                    return {
                        "status": "error",
                        "reason": "drain_release_failed",
                        "intent_id": resolved_id,
                        "drain_epoch": intent.drain_epoch,
                        "error": f"{type(exc).__name__}: {exc!r}",
                        "retryable": True,
                        "probe_error": f"{type(probe_exc).__name__}: {probe_exc!r}",
                    }
                if worker_intent_released(state, resolved_id):
                    drain_release = {
                        "recovered_after_error": f"{type(exc).__name__}: {exc!r}",
                        "drain_state": state,
                    }
                else:
                    return {
                        "status": "error",
                        "reason": "drain_release_failed",
                        "intent_id": resolved_id,
                        "drain_epoch": intent.drain_epoch,
                        "error": f"{type(exc).__name__}: {exc!r}",
                        "retryable": True,
                        "drain_state": state,
                    }
            else:
                return {
                    "status": "error",
                    "reason": "drain_release_failed",
                    "intent_id": resolved_id,
                    "drain_epoch": intent.drain_epoch,
                    "error": f"{type(exc).__name__}: {exc!r}",
                    "retryable": True,
                }

    from .drain_timeout_keep_await import cancel_or_join_supervise_task
    from .restart_window_ctl import clear_service_windows

    try:
        cancelled = store.cancel(resolved_id, reason="cancelled_by_operator")
    except RestartIntentCancelError as exc:
        return {
            "status": "refused",
            "reason": exc.reason,
            "intent_id": resolved_id,
            "intent_status": exc.status,
            "drain_release": drain_release,
        }
    if cancelled.service == "stargate":
        await cancel_or_join_supervise_task(cancelled.service)
        await clear_service_windows(store, cancelled.service, reason="intent_cancelled")
    return {
        "status": "cancelled",
        "intent_id": cancelled.intent_id,
        "service": cancelled.service,
        "intent_status": cancelled.status,
        "drain_epoch": cancelled.drain_epoch,
        "drain_release": drain_release,
        "already_cancelled": False,
    }
