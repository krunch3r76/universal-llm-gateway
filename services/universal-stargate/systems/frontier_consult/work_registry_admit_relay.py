"""Relay team_dispatch admits to GIW work-registry auto-record (best-effort)."""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from .cursor_sdk_worker_dispatch import _WORKER_TIMEOUT, worker_base_url

logger = get_logger(__name__)


async def relay_registry_record(
    *,
    request_id: str,
    dispatch_id: str,
    thread_id: str,
    caller_agent: str | None = None,
    seat_name: str | None = None,
    address: dict[str, Any] | None = None,
    work_key: str | None = None,
    resolved_model: str | None = None,
    contract: str | None = None,
) -> dict[str, Any] | None:
    """POST ``/api/v1/cursor/registry/record``. Failures are logged and swallowed."""
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
    }
    for key, val in (
        ("caller_agent", caller_agent),
        ("seat_name", seat_name),
        ("address", address),
        ("work_key", work_key),
        ("resolved_model", resolved_model),
        ("contract", contract),
    ):
        if val is not None:
            payload[key] = val
    try:
        async with make_async_client(
            worker_base_url(), timeout=_WORKER_TIMEOUT
        ) as client:
            resp = await client.post("/api/v1/cursor/registry/record", json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "registry record relay unreachable request_id=%s id=%s err=%s",
            request_id,
            dispatch_id,
            exc,
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "registry record relay refused request_id=%s id=%s status=%s",
            request_id,
            dispatch_id,
            resp.status_code,
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def relay_registry_terminal(
    *,
    request_id: str,
    dispatch_id: str,
    terminal_status: str,
    reason: str,
) -> dict[str, Any] | None:
    """POST ``/api/v1/cursor/registry/terminal``. Failures are logged and swallowed."""
    payload = {
        "dispatch_id": dispatch_id,
        "terminal_status": terminal_status,
        "reason": reason,
    }
    for attempt in range(2):
        try:
            async with make_async_client(
                worker_base_url(), timeout=_WORKER_TIMEOUT
            ) as client:
                resp = await client.post("/api/v1/cursor/registry/terminal", json=payload)
        except httpx.HTTPError as exc:
            if attempt == 0:
                continue
            logger.warning(
                "registry terminal relay unreachable request_id=%s id=%s err=%s",
                request_id,
                dispatch_id,
                exc,
            )
            return None
        if resp.status_code >= 500 and attempt == 0:
            continue
        if resp.status_code != 200:
            logger.warning(
                "registry terminal relay refused request_id=%s id=%s status=%s",
                request_id,
                dispatch_id,
                resp.status_code,
            )
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None
    return None


def attach_registry_next(
    result: dict[str, Any], relay_body: dict[str, Any] | None
) -> None:
    """Merge registry_row_id and _next from a relay body into a generate result."""
    if not relay_body or not relay_body.get("ok"):
        return
    row_id = relay_body.get("registry_row_id")
    if row_id:
        result["registry_row_id"] = row_id
    nxt = relay_body.get("_next")
    if isinstance(nxt, str) and nxt.strip():
        result["_next"] = nxt.strip()
