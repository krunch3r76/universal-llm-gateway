"""Thin Event Service resource client. Builds paths; does not read the catalog."""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import EVENTS_QUERY_SOCK, make_async_client, make_sync_client

from .query_client_errors import envelope_client_deadline

DEFAULT_EVENTS_QUERY_URL = f"unix://{EVENTS_QUERY_SOCK}"
_PREFIX = "/api/v1/observability"


def _non_scalar(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    bad = [key for key, value in params.items() if not _scalar(value)]
    if not bad:
        return None
    return {
        "error": f"Non-scalar params: {', '.join(bad)}",
        "code": "INVALID_PARAMS",
        "source": "rpc",
        "retryable": False,
        "data": {"invalid": bad},
        "status": 400,
    }


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _query_items(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        items.append((key, str(value)))
    return items


def _from_response(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if resp.status_code < 400:
        return body
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    message = body.get("message") or body.get("error") or resp.text
    out: dict[str, Any] = {
        "error": message,
        "code": body.get("code"),
        "source": body.get("source"),
        "retryable": body.get("retryable"),
        "data": data,
        "status": resp.status_code,
    }
    error_class = data.get("error_class") or body.get("error_class")
    if resp.status_code == 503 and body.get("code") == "LOCK_WAIT":
        error_class = error_class or "lock_wait"
    if error_class:
        out["error_class"] = error_class
    return out


def _transport_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.ReadTimeout | httpx.TimeoutException):
        env = envelope_client_deadline()
        env.update(
            {
                "code": "CLIENT_DEADLINE",
                "source": "rpc",
                "retryable": True,
                "data": {},
                "status": None,
            }
        )
        return env
    return {
        "error": str(exc),
        "code": "UPSTREAM_UNAVAILABLE",
        "source": "rpc",
        "retryable": True,
        "data": {},
        "status": None,
    }


def list_members(
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GET the category listing."""
    try:
        with make_sync_client(url, timeout=timeout) as client:
            return _from_response(client.get(prefix))
    except httpx.HTTPError as exc:
        return _transport_error(exc)


def query_member(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GET one member. Non-scalar params are rejected locally."""
    rejected = _non_scalar(params)
    if rejected is not None:
        return rejected
    try:
        with make_sync_client(url, timeout=timeout) as client:
            return _from_response(
                client.get(f"{prefix}/{name}", params=_query_items(params))
            )
    except httpx.HTTPError as exc:
        return _transport_error(exc)


def query_sql(
    sql: str,
    params: list[Any] | None = None,
    limit: int = 100,
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST the sql member."""
    body: dict[str, Any] = {"sql": sql, "limit": limit}
    if params is not None:
        body["params"] = params
    try:
        with make_sync_client(url, timeout=timeout) as client:
            return _from_response(client.post(f"{prefix}/sql", json=body))
    except httpx.HTTPError as exc:
        return _transport_error(exc)


async def list_members_async(
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Async GET of the category listing."""
    try:
        client = make_async_client(url, timeout=timeout)
        async with client:
            return _from_response(await client.get(prefix))
    except httpx.HTTPError as exc:
        return _transport_error(exc)


async def query_member_async(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Async GET of one member."""
    rejected = _non_scalar(params)
    if rejected is not None:
        return rejected
    try:
        client = make_async_client(url, timeout=timeout)
        async with client:
            return _from_response(
                await client.get(f"{prefix}/{name}", params=_query_items(params))
            )
    except httpx.HTTPError as exc:
        return _transport_error(exc)


async def query_sql_async(
    sql: str,
    params: list[Any] | None = None,
    limit: int = 100,
    *,
    url: str = DEFAULT_EVENTS_QUERY_URL,
    prefix: str = _PREFIX,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Async POST of the sql member."""
    body: dict[str, Any] = {"sql": sql, "limit": limit}
    if params is not None:
        body["params"] = params
    try:
        client = make_async_client(url, timeout=timeout)
        async with client:
            return _from_response(await client.post(f"{prefix}/sql", json=body))
    except httpx.HTTPError as exc:
        return _transport_error(exc)
