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
    caller_dispatch_id: str | None = None,
    work_key: str | None = None,
    thread_id: str | None = None,
    claim_id: str | None = None,
    outcome: str | None = None,
    ttl_s: int | None = None,
    limit: int | None = None,
    window_hours: int | None = None,
    seat_name: str | None = None,
    address: dict[str, Any] | None = None,
    work_status: str | None = None,
    address_patch: dict[str, Any] | None = None,
    note: str | None = None,
    state: str | None = None,
    work_key_prefix: str | None = None,
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
        if seat_name is not None:
            payload["seat_name"] = seat_name
        if address is not None:
            payload["address"] = address
        if work_status is not None:
            payload["work_status"] = work_status
        ident = work_key or ""
    elif op == "claim_update":
        path = f"/api/v1/cursor/claim/{claim_id}/update"
        method = "POST"
        payload: dict[str, Any] = {"holder_agent": holder_agent}
        if caller_dispatch_id:
            payload["caller_dispatch_id"] = caller_dispatch_id
        if work_status is not None:
            payload["work_status"] = work_status
        if address_patch is not None:
            payload["address_patch"] = address_patch
        if note is not None:
            payload["note"] = note
        ident = claim_id or ""
    elif op == "claim_release":
        path = f"/api/v1/cursor/claim/{claim_id}/release"
        method = "POST"
        payload = {"holder_agent": holder_agent, "outcome": outcome}
        if caller_dispatch_id:
            payload["caller_dispatch_id"] = caller_dispatch_id
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
    elif op == "registry":
        path = "/api/v1/cursor/registry"
        method = "GET"
        payload = {}
        ident = work_key_prefix or "registry"
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
                params: dict[str, Any] = {}
                if op == "occupancy":
                    params["work_key"] = work_key
                elif op == "ledger":
                    params["work_key"] = work_key
                    if limit is not None:
                        params["limit"] = limit
                    if window_hours is not None:
                        params["window_hours"] = window_hours
                elif op == "registry":
                    params["state"] = state or "live"
                    if window_hours is not None:
                        params["window_hours"] = window_hours
                    if work_key_prefix is not None:
                        params["work_key_prefix"] = work_key_prefix
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
