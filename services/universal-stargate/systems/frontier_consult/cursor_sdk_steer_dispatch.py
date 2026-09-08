"""Thin Stargate relay: ``team_dispatch(op=steer)`` → GIW park route (R5)."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from .cursor_sdk_worker_dispatch import (
    _WORKER_TIMEOUT,
    _parse_worker_error,
    _parse_worker_success,
    _transport_failure_detail,
    worker_base_url,
)

logger = get_logger(__name__)

SteerKind = Literal["park_for_restart"]


async def steer_park_for_restart(
    *,
    request_id: str,
    dispatch_id: str,
    reason: str,
    actor: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/cursor/dispatch/{dispatch_id}/park``; propagate GIW envelope."""
    payload: dict[str, object] = {
        "reason": reason,
        "actor": actor or "stargate-steer",
        "mode": "cancel",
    }
    try:
        async with make_async_client(
            worker_base_url(), timeout=_WORKER_TIMEOUT
        ) as client:
            resp = await client.post(
                f"/api/v1/cursor/dispatch/{dispatch_id}/park",
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk steer park unreachable: request_id=%s dispatch_id=%s err=%s",
            request_id,
            dispatch_id,
            exc,
        )
        return False, _transport_failure_detail(dispatch_id=dispatch_id, exc=exc)
    if resp.status_code in (200, 202):
        detail = _parse_worker_success(resp)
        detail.setdefault("dispatch_id", dispatch_id)
        detail["steer"] = "park_for_restart"
        return True, detail
    logger.warning(
        "cursor-sdk steer park refused: request_id=%s dispatch_id=%s status=%s body=%s",
        request_id,
        dispatch_id,
        resp.status_code,
        resp.text[:200],
    )
    return False, _parse_worker_error(resp, dispatch_id=dispatch_id)
