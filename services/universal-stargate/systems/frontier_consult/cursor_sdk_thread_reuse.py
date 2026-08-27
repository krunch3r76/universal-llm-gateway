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


CONDUCTOR_COORD_SPLIT_CODE = "conductor_coord_split_refused"
CONDUCTOR_COORD_SPLIT_HINT = (
    "Legal conductor shapes: dispatch_thread_id=<continuity root with turns> "
    "(Stargate mints a worker child of the root); or pre-create a child with "
    "lifecycle_state=pending and turn_count==0 and pass that id; or re-admit "
    "with reuse_thread=<work thread>. Lifecycle-null empty threads are refused."
)


def _is_continuity_root(payload: dict[str, Any]) -> bool:
    """True when the probed thread is a continuity root that may mint a worker child."""
    turn_count = int(payload.get("turn_count") or 0)
    if turn_count < 1:
        return False
    tags = payload.get("tags") or []
    if "role:root" in tags:
        return True
    return not payload.get("parent_thread")


def _is_pending_empty_child(payload: dict[str, Any]) -> bool:
    """Pending shell that is a child of a root — legal conductor reuse (shape 2)."""
    parent = payload.get("parent_thread")
    return (
        payload.get("bus_lifecycle_state") == "pending"
        and int(payload.get("turn_count") or 0) == 0
        and bool(parent)
    )


def _conductor_coord_split_error(request_id: str):
    """Build the 422 envelope — hint names root, pending child, and reuse_thread=."""
    from .admission import FrontierEndpointError

    return FrontierEndpointError(
        request_id=request_id,
        field="dispatch_thread_id",
        reason=CONDUCTOR_COORD_SPLIT_HINT,
        status_code=422,
        code=CONDUCTOR_COORD_SPLIT_CODE,
        details={"hint": CONDUCTOR_COORD_SPLIT_HINT},
    )


async def refuse_conductor_coord_split(
    *,
    request_id: str,
    packet_kind: str | None,
    reuse_thread: str | None,
    dispatch_thread_id: str | None,
) -> None:
    """Raise 422 when conductor generate would mint a grandchild coord split.

    Legal: explicit ``reuse_thread`` (re-admit); pending-empty *child*; continuity
    root with turns (mint child). Probe failure is fail-closed for conductor.
    """
    if (packet_kind or "").strip().lower() != "conductor":
        return
    explicit = reuse_thread.strip() if reuse_thread and reuse_thread.strip() else None
    if explicit is not None:
        return
    arc = (
        dispatch_thread_id.strip()
        if dispatch_thread_id and dispatch_thread_id.strip()
        else None
    )
    if arc is None or not arc.isdigit():
        raise _conductor_coord_split_error(request_id)
    payload = await probe_thread(arc)
    if payload is None:
        raise _conductor_coord_split_error(request_id)
    if _is_pending_empty_child(payload) or _is_continuity_root(payload):
        return
    raise _conductor_coord_split_error(request_id)


async def resolve_cursor_sdk_thread_targets(
    *,
    reuse_thread: str | None,
    dispatch_thread_id: str | None,
    packet_kind: str | None = None,
    request_id: str = "",
) -> tuple[str | None, str | None, bool]:
    """Return ``(reuse_thread, parent_dispatch_thread_id, is_auto_consolidation)``.

    Thin delegate over ``resolve_generate_thread_targets`` for cursor-sdk lane.
    Conductor ``packet_kind`` refuses grandchild coord-split (422) before resolve.
    """
    await refuse_conductor_coord_split(
        request_id=request_id,
        packet_kind=packet_kind,
        reuse_thread=reuse_thread,
        dispatch_thread_id=dispatch_thread_id,
    )
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
