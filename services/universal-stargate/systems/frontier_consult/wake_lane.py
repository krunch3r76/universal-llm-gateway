"""Validate opt-in ``wake_lane`` on team_dispatch generate."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .admission import FrontierEndpointError
from .cursor_sdk_thread_reuse import _bus_headers, _bus_token_configured

_WAKE_RE = re.compile(r"^\d{1,10}$")

ThreadFetcher = Callable[[str], Awaitable[dict | None]]


class WakeLaneUnreachableError(Exception):
    """The bus could not be asked. This is not an invalid lane."""


def wake_lane_needs_probe(
    *,
    op: str,
    wake_lane: str | None,
    hop_from: str | None,
) -> bool:
    """Hop admits already inherited a probed lane. Do not probe them again."""
    return op == "generate" and wake_lane is not None and not (hop_from or "").strip()


async def _probe_wake_thread(thread_id: str) -> dict | None:
    """Return the thread on HTTP 200, None on 404.

    Transport errors and non-404 failures raise ``WakeLaneUnreachableError``.
    ``probe_thread`` collapses those to None for thread reuse; this probe
    must not, or a bus blip becomes 422 ``wake_lane_invalid``.
    """
    if not _bus_token_configured() or not thread_id.strip().isdigit():
        raise WakeLaneUnreachableError("bus token unset or thread id not numeric")
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(
                f"/threads/{thread_id.strip()}", headers=_bus_headers()
            )
    except httpx.HTTPError as exc:
        raise WakeLaneUnreachableError(type(exc).__name__) from exc
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise WakeLaneUnreachableError(f"http_{resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise WakeLaneUnreachableError("non_object")
    return payload


async def validate_wake_lane(
    wake_lane: str | None,
    *,
    request_id: str,
    fetch_thread: ThreadFetcher | None = None,
) -> None:
    """Require ``^\\d{1,10}$``, an existing thread, and tag ``role:root``.

    A missing thread or a missing tag is 422 ``wake_lane_invalid``. A bus
    that cannot answer is 503 ``wake_lane_unavailable``. ``fetch_thread``
    is the test seam; raise ``WakeLaneUnreachableError`` from it for the 503 path.
    """
    text = wake_lane or ""
    if not _WAKE_RE.fullmatch(text):
        raise FrontierEndpointError(
            request_id=request_id,
            field="wake_lane",
            reason="wake_lane must be a numeric thread id",
            status_code=422,
            code="wake_lane_invalid",
        )
    try:
        payload = (
            await fetch_thread(text)
            if fetch_thread is not None
            else await _probe_wake_thread(text)
        )
    except WakeLaneUnreachableError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="wake_lane",
            reason=f"wake_lane thread probe failed: {exc}",
            status_code=503,
            code="wake_lane_unavailable",
        ) from exc
    tags = list((payload or {}).get("tags") or [])
    if payload is None or "role:root" not in tags:
        raise FrontierEndpointError(
            request_id=request_id,
            field="wake_lane",
            reason="wake_lane must name an existing role:root thread",
            status_code=422,
            code="wake_lane_invalid",
        )
