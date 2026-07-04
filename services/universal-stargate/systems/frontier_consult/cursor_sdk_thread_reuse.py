"""Resolve worker vs coordination threads for single-thread Q/R (generate paths)."""

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


async def probe_thread(thread_id: str) -> dict[str, Any] | None:
    """GET /threads/{id}; return payload on HTTP 200, else None."""
    if not _bus_token_configured() or not thread_id.strip().isdigit():
        return None
    normalized = thread_id.strip()
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(f"/threads/{normalized}", headers=_bus_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "thread reuse probe failed: thread=%s err=%s", normalized, exc
        )
        return None
    if resp.status_code != 200:
        return None
    payload: dict[str, Any] = resp.json()
    return payload


async def is_pending_empty_worker_thread(thread_id: str) -> bool:
    """True when *thread_id* is a create_thread pending shell with no turns yet."""
    payload = await probe_thread(thread_id)
    if payload is None:
        return False
    return (
        payload.get("bus_lifecycle_state") == "pending"
        and int(payload.get("turn_count") or 0) == 0
    )


async def resolve_generate_thread_targets(
    *,
    reuse_thread: str | None,
    dispatch_thread_id: str | None,
    role_lane: str,
    split_thread: bool = False,
) -> tuple[str | None, str | None, bool, int]:
    """Return ``(reuse_thread, parent_dispatch_thread_id, is_auto, reuse_after_turn)``.

    Single-thread Q/R: when the worker thread is also the query surface, omit the
    parent coord pointer (``parent_dispatch_thread_id=None``).
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
        probed = await probe_thread(explicit_reuse)
        reuse_after_turn = int(probed.get("turn_count") or 0) if probed else 0
        if arc_id is not None and explicit_reuse == arc_id:
            return explicit_reuse, None, False, reuse_after_turn
        return explicit_reuse, arc_id, False, reuse_after_turn

    if role_lane == "cursor-sdk":
        if arc_id is not None and arc_id.isdigit():
            if await is_pending_empty_worker_thread(arc_id):
                return arc_id, None, True, 0
        return None, arc_id, False, 0

    if role_lane == "api":
        if split_thread:
            return None, arc_id, False, 0
        if arc_id is not None and arc_id.isdigit():
            probed = await probe_thread(arc_id)
            if probed is not None:
                status = probed.get("status")
                turn_count = int(probed.get("turn_count") or 0)
                if status != "closed" and turn_count >= 1:
                    return arc_id, None, True, turn_count
        return None, arc_id, False, 0

    return None, arc_id, False, 0


async def resolve_cursor_sdk_thread_targets(
    *,
    reuse_thread: str | None,
    dispatch_thread_id: str | None,
) -> tuple[str | None, str | None, bool]:
    """Return ``(reuse_thread, parent_dispatch_thread_id, is_auto_consolidation)``.

    Thin delegate over ``resolve_generate_thread_targets`` for cursor-sdk lane.
    """
    reuse, parent, is_auto, _reuse_after_turn = await resolve_generate_thread_targets(
        reuse_thread=reuse_thread,
        dispatch_thread_id=dispatch_thread_id,
        role_lane="cursor-sdk",
    )
    return reuse, parent, is_auto


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


def api_split_warning(
    *,
    reuse_thread: str | None,
    parent_dispatch_thread_id: str | None,
    split_thread: bool,
) -> str | None:
    """Advisory when api-role generate minted a sibling instead of auto-reusing."""
    if split_thread:
        return None
    if reuse_thread is not None:
        return None
    arc = (parent_dispatch_thread_id or "").strip()
    if not arc.isdigit():
        return None
    return (
        f"api-role generate minted a separate result thread on active arc {arc}: "
        "dispatch thread was not reusable (closed/unreachable/no turns). Pass "
        "reuse_thread=<thread> to consolidate or split_thread=true to silence."
    )
