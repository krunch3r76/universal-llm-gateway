"""Orphan tracking for cursor-sdk outer-timeout dispatches.

When the async outer watchdog fires, the sync worker thread is intentionally
non-cancellable. Without explicit orphan handling, heartbeats keep emitting
``frontier.sdk.worker.progress`` and an active tool leg can reset the httpx
read deadline indefinitely — observability and operators see FAILED on the bus
while the bridge keeps running (friction 23851).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from cursor_sdk import Client

logger = get_logger(__name__)

_lock = threading.Lock()
_active_clients: dict[str, Client] = {}
_orphaned: set[str] = set()


def register_active_client(*, dispatch_id: str, client: Client) -> None:
    with _lock:
        _active_clients[dispatch_id] = client


def unregister_active_client(*, dispatch_id: str) -> Client | None:
    with _lock:
        return _active_clients.pop(dispatch_id, None)


def is_dispatch_orphaned(*, dispatch_id: str) -> bool:
    with _lock:
        return dispatch_id in _orphaned


def mark_dispatch_orphaned(*, dispatch_id: str) -> Client | None:
    """Flag dispatch orphaned and return the live bridge client, if any."""
    with _lock:
        _orphaned.add(dispatch_id)
        return _active_clients.get(dispatch_id)


def clear_dispatch_orphan_state(*, dispatch_id: str) -> None:
    with _lock:
        _orphaned.discard(dispatch_id)
        _active_clients.pop(dispatch_id, None)


def abort_orphaned_bridge(*, dispatch_id: str, client: Client | None = None) -> bool:
    """Hard-close the bridge subprocess for a timed-out orphan dispatch."""
    with _lock:
        owned = _active_clients.pop(dispatch_id, None)
    target = owned or client
    if target is None:
        return False
    try:
        target.close()
    except Exception as exc:  # noqa: BLE001 — best-effort kill must not wedge timeout path
        logger.warning(
            "orphan bridge close failed: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return False
    logger.warning(
        "orphan bridge aborted after outer timeout: dispatch_id=%s",
        dispatch_id,
    )
    return True
