"""Thin Stargate relay: team_dispatch claim ops → GIW claim routes.

Same client and fail-closed status mapping as ``op=steer``: GIW 404, 409, 422,
and 503 pass through. ``holder_agent`` is the resolved caller, never a free
text field on the body.
"""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from .cursor_sdk_worker_dispatch import (
    _WORKER_TIMEOUT,
    _parse_worker_error,
    _transport_failure_detail,
    worker_base_url,
)

logger = get_logger(__name__)


def _success(resp: httpx.Response) -> dict[str, Any]:
    """Parse a GIW JSON body and attach the HTTP status_code."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {"message": str(body)}
    body["status_code"] = resp.status_code
    return body


async def relay_house_claim(
    *,
    request_id: str,
    op: str,
    holder_agent: str,
    work_key: str | None = None,
    thread_id: str | None = None,
    claim_id: str | None = None,
    outcome: str | None = None,
    ttl_s: int | None = None,
    limit: int | None = None,
    window_hours: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Forward claim, claim_release, occupancy, or ledger to the matching GIW route.

    Returns ``(ok, body)``. ``ok`` is true only for HTTP 200. Other GIW
    statuses are the worker envelope with ``status_code`` set.
    """
    if op == "claim":
        path = "/api/v1/cursor/claim"
        method = "POST"
        payload: dict[str, Any] = {
            "work_key": work_key,
            "holder_agent": holder_agent,
            "thread_id": thread_id,
        }
        if ttl_s is not None:
            payload["ttl_s"] = ttl_s
        ident = work_key or ""
    elif op == "claim_release":
        path = f"/api/v1/cursor/claim/{claim_id}/release"
        method = "POST"
        payload = {"holder_agent": holder_agent, "outcome": outcome}
        ident = claim_id or ""
    elif op == "occupancy":
        path = "/api/v1/cursor/occupancy"
        method = "GET"
        payload = {}
        ident = work_key or ""
    elif op == "ledger":
        path = "/api/v1/cursor/work-key-ledger"
        method = "GET"
        payload = {}
        ident = work_key or ""
    else:
        return False, {
            "status_code": 422,
            "code": "claim_op_unsupported",
            "message": f"unsupported claim op {op!r}",
        }
    try:
        async with make_async_client(
            worker_base_url(), timeout=_WORKER_TIMEOUT
        ) as client:
            if method == "GET":
                params: dict[str, Any] = {"work_key": work_key}
                if op == "ledger":
                    if limit is not None:
                        params["limit"] = limit
                    if window_hours is not None:
                        params["window_hours"] = window_hours
                resp = await client.get(path, params=params)
            else:
                resp = await client.post(path, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk claim relay unreachable: request_id=%s op=%s err=%s",
            request_id,
            op,
            exc,
        )
        return False, _transport_failure_detail(dispatch_id=ident, exc=exc)
    if resp.status_code == 200:
        return True, _success(resp)
    logger.warning(
        "cursor-sdk claim relay refused: request_id=%s op=%s status=%s body=%s",
        request_id,
        op,
        resp.status_code,
        resp.text[:200],
    )
    return False, _parse_worker_error(resp, dispatch_id=ident)
