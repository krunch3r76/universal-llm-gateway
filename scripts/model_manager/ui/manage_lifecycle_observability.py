"""Manage lifecycle attribution — events, logs, window reason, request context."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import socket as sock_mod
import struct
from typing import Any

from scripts.model_manager.observation_event import emit_manage_lifecycle_requested

logger = logging.getLogger(__name__)

_MANAGE_PARAMS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "manage_request_params", default=None
)

LIFECYCLE_API_ACTIONS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "sync_restart",
        "rebuild",
        "recycle_giw",
        "fleet_sync_restart",
        "fleet_rebuild_deploy",
    }
)


def _optional_str(params: dict[str, Any], key: str) -> str | None:
    raw = params.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def manage_caller_identity(params: dict[str, Any]) -> str | None:
    """Same precedence as fleet attribution — single source for manage + fleet."""
    dispatch_id = _optional_str(params, "caller_dispatch_id")
    if dispatch_id:
        return dispatch_id
    for key in ("caller_agent", "from_agent", "agent", "caller"):
        val = _optional_str(params, key)
        if val:
            return val
    return None


def manage_caller_reason(params: dict[str, Any]) -> str | None:
    return _optional_str(params, "reason")


def manage_window_reason(action: str, params: dict[str, Any] | None = None) -> str:
    """AC1: window reason includes optional caller ``reason`` from the request."""
    p = params if params is not None else _MANAGE_PARAMS.get() or {}
    caller_reason = manage_caller_reason(p)
    base = f"manage {action}"
    if caller_reason:
        return f"{base}: {caller_reason}"
    return base


def bind_manage_request_params(params: dict[str, Any]) -> contextvars.Token[dict[str, Any] | None]:
    return _MANAGE_PARAMS.set(dict(params))


def reset_manage_request_params(token: contextvars.Token[dict[str, Any] | None]) -> None:
    _MANAGE_PARAMS.reset(token)


def peer_credentials_from_socket(
    sock: Any | None,
) -> tuple[int | None, int | None]:
    """Linux SO_PEERCRED for UDS peer pid/uid; absent when unavailable."""
    if sock is None:
        return None, None
    so_peercred = getattr(sock_mod, "SO_PEERCRED", None)
    if so_peercred is None:
        return None, None
    try:
        raw = sock.getsockopt(sock_mod.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
        pid, uid, _gid = struct.unpack("3i", raw)
        return pid, uid
    except OSError:
        return None, None


async def record_manage_lifecycle_requested(
    *,
    action: str,
    service: str | None,
    reason: str | None,
    caller: str | None,
    caller_thread_id: str | None,
    force: bool | None,
    peer_pid: int | None,
    peer_uid: int | None,
) -> None:
    logger.info(
        "manage.lifecycle.requested action=%s service=%s reason=%s caller=%s "
        "caller_thread_id=%s force=%s peer_pid=%s peer_uid=%s",
        action,
        service,
        reason,
        caller,
        caller_thread_id,
        force,
        peer_pid,
        peer_uid,
    )
    with contextlib.suppress(Exception):
        await emit_manage_lifecycle_requested(
            action=action,
            service=service,
            reason=reason,
            caller=caller,
            caller_thread_id=caller_thread_id,
            force=force,
            peer_pid=peer_pid,
            peer_uid=peer_uid,
        )
