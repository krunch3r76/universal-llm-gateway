"""Relay team_dispatch admits to GIW work-registry auto-record (best-effort)."""

from __future__ import annotations

import os
from typing import Any

import httpx
from giw_endpoint import git_integration_worker_base_url
from transport_utils import make_async_client
from universal_logging import get_logger

from .cursor_sdk_worker_dispatch import _WORKER_TIMEOUT, worker_base_url

logger = get_logger(__name__)

_LIVE_GIW_DEFAULT_URL = "http://127.0.0.1:8091"
_REGISTRY_RELAY_INERT_MODE = "pytest_inert"


def _registry_relay_would_hit_live_giw() -> bool:
    """True when pytest inert mode is on but env still points at the default GIW."""
    if os.environ.get("GIW_REGISTRY_RELAY_MODE") != _REGISTRY_RELAY_INERT_MODE:
        return False
    return git_integration_worker_base_url() == _LIVE_GIW_DEFAULT_URL


def _block_live_registry_relay_under_pytest(
    *, request_id: str, dispatch_id: str, op: str
) -> bool:
    if not _registry_relay_would_hit_live_giw():
        return False
    logger.warning(
        "registry %s relay blocked under pytest inert mode request_id=%s id=%s",
        op,
        request_id,
        dispatch_id,
    )
    return True


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
    max_wall_s: int | None = None,
    prompt_bytes: int | None = None,
) -> dict[str, Any] | None:
    """POST ``/api/v1/cursor/registry/record``. Failures are logged and swallowed."""
    if _block_live_registry_relay_under_pytest(
        request_id=request_id, dispatch_id=dispatch_id, op="record"
    ):
        return None
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
        ("max_wall_s", max_wall_s),
        ("prompt_bytes", prompt_bytes),
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
    harvest_chars: int | None = None,
) -> dict[str, Any] | None:
    """POST ``/api/v1/cursor/registry/terminal``. Failures are logged and swallowed."""
    if _block_live_registry_relay_under_pytest(
        request_id=request_id, dispatch_id=dispatch_id, op="terminal"
    ):
        return None
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "terminal_status": terminal_status,
        "reason": reason,
    }
    if harvest_chars is not None:
        payload["harvest_chars"] = harvest_chars
    for attempt in range(2):
        try:
            async with make_async_client(
                worker_base_url(), timeout=_WORKER_TIMEOUT
            ) as client:
                resp = await client.post(
                    "/api/v1/cursor/registry/terminal", json=payload
                )
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


async def relay_registry_heartbeat(
    *,
    request_id: str,
    dispatch_id: str,
) -> dict[str, Any] | None:
    """POST ``/api/v1/cursor/registry/heartbeat``. Failures are logged and swallowed."""
    if _block_live_registry_relay_under_pytest(
        request_id=request_id, dispatch_id=dispatch_id, op="heartbeat"
    ):
        return None
    payload = {"dispatch_id": dispatch_id}
    for attempt in range(2):
        try:
            async with make_async_client(
                worker_base_url(), timeout=_WORKER_TIMEOUT
            ) as client:
                resp = await client.post(
                    "/api/v1/cursor/registry/heartbeat", json=payload
                )
        except httpx.HTTPError as exc:
            if attempt == 0:
                continue
            logger.warning(
                "registry heartbeat relay unreachable request_id=%s id=%s err=%s",
                request_id,
                dispatch_id,
                exc,
            )
            return None
        if resp.status_code >= 500 and attempt == 0:
            continue
        if resp.status_code != 200:
            logger.warning(
                "registry heartbeat relay refused request_id=%s id=%s status=%s",
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
