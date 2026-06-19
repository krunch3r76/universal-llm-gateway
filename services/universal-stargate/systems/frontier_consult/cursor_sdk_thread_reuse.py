"""Resolve cursor-sdk worker vs coordination threads for single-thread Q/R."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_token_configured() -> bool:
    if os.getenv("AGENT_BUS_TOKEN", "").strip():
        return True
    return os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


async def is_pending_empty_worker_thread(thread_id: str) -> bool:
    """True when *thread_id* is a create_thread pending shell with no turns yet."""
    if not _bus_token_configured() or not thread_id.strip().isdigit():
        return False
    normalized = thread_id.strip()
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(f"/threads/{normalized}", headers=_bus_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk reuse probe failed: thread=%s err=%s", normalized, exc
        )
        return False
    if resp.status_code != 200:
        return False
    payload: dict[str, Any] = resp.json()
    return (
        payload.get("bus_lifecycle_state") == "pending"
        and int(payload.get("turn_count") or 0) == 0
    )


async def resolve_cursor_sdk_thread_targets(
    *,
    reuse_thread: str | None,
    dispatch_thread_id: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(reuse_thread, parent_dispatch_thread_id)`` for SDK generate.

    Single-thread Q/R: when the worker thread is also the query surface, omit the
    parent coord pointer (``parent_dispatch_thread_id=None``).

    - Explicit ``reuse_thread`` consolidates on that thread; ``dispatch_thread_id``
      remains the arc coordination thread when it differs.
    - Numeric ``dispatch_thread_id`` that names a pending empty shell (typical
      ``create_thread(lifecycle_state=pending)`` pre-stage) auto-reuses instead of
      minting a sibling worker thread.
    - Active arc threads (turns present) keep the worker/coord split.
    """
    explicit_reuse = (
        reuse_thread.strip() if reuse_thread and reuse_thread.strip() else None
    )
    arc_id = (
        dispatch_thread_id.strip()
        if dispatch_thread_id and dispatch_thread_id.strip()
        else None
    )

    if explicit_reuse is not None:
        if arc_id is not None and explicit_reuse == arc_id:
            return explicit_reuse, None
        return explicit_reuse, arc_id

    if arc_id is not None and arc_id.isdigit():
        if await is_pending_empty_worker_thread(arc_id):
            return arc_id, None

    return None, arc_id


def consolidation_split_warning(
    *,
    reuse_thread: str | None,
    parent_dispatch_thread_id: str | None,
) -> str | None:
    """Advisory when a cursor-sdk generate keeps the worker/coord split on a
    numeric active arc — a sibling worker thread was minted instead of
    consolidating Q/R onto one thread.

    Fires only when no ``reuse_thread`` was resolved AND the coordination
    (parent) thread is a numeric arc. Slug coordination threads are split by
    design and do not warn.
    """
    if reuse_thread is not None:
        return None
    arc = (parent_dispatch_thread_id or "").strip()
    if not arc.isdigit():
        return None
    return (
        f"cursor-sdk generate kept the worker/coord split on active arc {arc}: "
        "a sibling worker thread was minted (Q/R not consolidated). To "
        "consolidate, pass reuse_thread=<thread> or set dispatch_thread_id to a "
        "pending pre-created thread."
    )
