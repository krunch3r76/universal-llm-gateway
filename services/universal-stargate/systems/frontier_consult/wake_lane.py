"""Validate opt-in ``wake_lane`` on team_dispatch generate."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from .admission import FrontierEndpointError
from .cursor_sdk_thread_reuse import probe_thread

_WAKE_RE = re.compile(r"^\d{1,10}$")

ThreadFetcher = Callable[[str], Awaitable[dict | None]]


async def validate_wake_lane(
    wake_lane: str | None,
    *,
    request_id: str,
    fetch_thread: ThreadFetcher | None = None,
) -> None:
    """Require ``^\\d{1,10}$``, an existing thread, and tag ``role:root``.

    Anything else raises 422 ``wake_lane_invalid``. ``fetch_thread`` is the
    test seam; production uses ``probe_thread``.
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
    fetcher = fetch_thread or probe_thread
    payload = await fetcher(text)
    tags = list((payload or {}).get("tags") or [])
    if payload is None or "role:root" not in tags:
        raise FrontierEndpointError(
            request_id=request_id,
            field="wake_lane",
            reason="wake_lane must name an existing role:root thread",
            status_code=422,
            code="wake_lane_invalid",
        )
