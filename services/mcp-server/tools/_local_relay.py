"""Shared HTTP relay to internal services (UDS or Docker network).

Infrastructure helper used by proxy modules and the ``local_api`` MCP tool.
"""

from __future__ import annotations

import json as _json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_EMAIL_BRIDGE_URL,
    make_sync_client,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0
_ROUTE_TIMEOUTS: dict[tuple[str, str, str], float] = {
    ("email-bridge", "POST", "/ingest"): 120.0,
    ("email-bridge", "POST", "/pull"): 120.0,
}
# Parameterized routes (path embeds an id) matched by suffix. review_extract
# (POST /review/{message_id}/extract) runs the probate-eml-extract pipeline —
# two sonnet stages, pipeline options.timeout_seconds=180 — so the relay budget
# must exceed the pipeline budget or the client aborts a still-running extract.
# review_dismiss has no LLM stage and stays on the default budget.
_ROUTE_SUFFIX_TIMEOUTS: list[tuple[str, str, str, float]] = [
    ("email-bridge", "POST", "/extract", 200.0),
    # Handoff wait long-polls server-side up to MAX_WAIT_SECONDS (60); the relay
    # budget must exceed it or the client aborts a still-blocking wait.
    ("agent-bus", "GET", "/wait", 75.0),
]

_SERVICES: dict[str, dict[str, str]] = {
    "journal-bridge": {
        "url": "http://journal-bridge:8200",
        "token_env": "BRIDGE_TOKEN",
    },
    "agent-bus": {
        "url": DEFAULT_AGENT_BUS_URL,
        "token_env": "AGENT_BUS_TOKEN",
    },
    "cortex-api": {
        "url": DEFAULT_CORTEX_URL,
    },
    "email-bridge": {
        "url": DEFAULT_EMAIL_BRIDGE_URL,
    },
}


def resolve_timeout(service: str, method: str, path: str) -> float:
    """Return the client budget for a local relay route.

    Exact (service, method, path) match wins; otherwise a suffix rule matches
    parameterized routes whose path embeds an id; otherwise the default budget.
    """
    method = method.upper()
    exact = _ROUTE_TIMEOUTS.get((service, method, path))
    if exact is not None:
        return exact
    path_no_query = path.split("?", 1)[0]
    for svc, mth, suffix, timeout in _ROUTE_SUFFIX_TIMEOUTS:
        if service == svc and method == mth and path_no_query.endswith(suffix):
            return timeout
    return _REQUEST_TIMEOUT


def _request_with_wall_clock(
    client: httpx.Client,
    *,
    method: str,
    path: str,
    json_body: dict[str, Any] | None,
    headers: dict[str, str],
    wall_clock_s: float,
) -> httpx.Response:
    """Run ``client.request`` under a hard wall-clock ceiling.

    httpx timeouts usually abort idle reads, but friction 23653 showed an
    intermittent orphan where ``mcp.local.api.called`` never gained a twin
    ``completed``/``failed`` despite agent-bus logging HTTP 200 for ``/wait``.
    ``Future.result(timeout=…)`` returns to the MCP tool even if the worker
    thread remains stuck; closing the client best-effort unblocks that thread.
    ``shutdown(wait=False)`` keeps the MCP tool path from blocking on the
    orphaned worker during executor teardown.
    """

    def _call() -> httpx.Response:
        return client.request(method, path, json=json_body, headers=headers)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_call)
        try:
            return fut.result(timeout=wall_clock_s)
        except FuturesTimeoutError:
            try:
                client.close()
            except Exception:
                logger.warning(
                    "wall-clock timeout: client.close() failed for %s %s",
                    method,
                    path,
                    exc_info=True,
                )
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def relay(
    service: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Forward an HTTP request to an internal service (UDS or Docker network).

    Returns:
        Parsed JSON response from the service, or ``{"error": "<message>"}``.
    """
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return {"error": f"Unsupported HTTP method: {method!r}"}

    if isinstance(body, str):
        try:
            body = _json.loads(body)
        except (ValueError, TypeError):
            return {"error": f"body is a string but not valid JSON: {body[:200]}"}

    svc_config = _SERVICES.get(service)
    if svc_config is None:
        return {
            "error": (f"Unknown service: {service!r}. Available: {sorted(_SERVICES)}")
        }

    service_url = svc_config["url"]
    request_timeout = resolve_timeout(service, method, path)

    token_env = svc_config.get("token_env", "")
    bearer = token or (os.environ.get(token_env, "") if token_env else "")
    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    def _record_failed(
        *,
        error: str,
        duration: float,
        status: int | None = None,
        detail: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "service": service,
            "method": method,
            "path": path,
            "error": error,
            "duration_s": round(duration, 3),
            "timeout_s": request_timeout,
            **({"status": status} if status is not None else {}),
            **({"detail": detail} if detail else {}),
        }
        record("mcp.local.api.failed", **payload)

    t0 = monotonic_now()
    record(
        "mcp.local.api.called",
        service=service,
        method=method,
        path=path,
        timeout_s=request_timeout,
    )

    client: httpx.Client | None = None
    try:
        client = make_sync_client(service_url, timeout=request_timeout)
        try:
            response = _request_with_wall_clock(
                client,
                method=method,
                path=path,
                json_body=body,
                headers=headers,
                wall_clock_s=request_timeout,
            )
        except FuturesTimeoutError as exc:
            duration = monotonic_now() - t0
            _record_failed(
                error="wall_clock_timeout",
                duration=duration,
                detail=str(exc) or f"no twin within {request_timeout}s",
            )
            return {"error": f"Request to {service} timed out"}

        duration = monotonic_now() - t0

        if response.status_code >= 400:
            _record_failed(
                error="http_error",
                status=response.status_code,
                duration=duration,
            )
            err: dict[str, Any] = {
                "error": f"HTTP {response.status_code}",
                "status_code": response.status_code,
                "body": response.text,
            }
            # Surface structured FastAPI `detail` payloads (e.g. 413
            # body_too_large) so callers can discriminate on `reason`
            # without parsing the body string.
            try:
                parsed_err = response.json()
            except Exception:
                parsed_err = None
            if isinstance(parsed_err, dict):
                detail_value = parsed_err.get("detail")
                if detail_value is not None:
                    err["detail"] = detail_value
            return err

        try:
            parsed = response.json()
        except Exception as exc:
            logger.warning(
                "Failed to parse JSON response from %s %s %s: %s",
                service,
                method,
                path,
                exc,
                exc_info=True,
            )
            _record_failed(
                error="invalid_json",
                status=response.status_code,
                duration=duration,
                detail=str(exc),
            )
            return {
                "error": "Invalid JSON response",
                "detail": str(exc),
                "text": response.text,
            }

        record(
            "mcp.local.api.completed",
            service=service,
            method=method,
            path=path,
            status=response.status_code,
            duration_s=round(duration, 3),
            timeout_s=request_timeout,
        )
        return parsed

    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        if isinstance(exc, httpx.ConnectError):
            _record_failed(error="connect_error", duration=duration, detail=str(exc))
            return {"error": f"Connection failed to {service}"}
        if isinstance(exc, httpx.TimeoutException):
            _record_failed(error="timeout", duration=duration, detail=str(exc))
            return {"error": f"Request to {service} timed out"}
        _record_failed(error="request_error", duration=duration, detail=str(exc))
        return {"error": f"Request to {service} failed"}
    except Exception as exc:
        duration = monotonic_now() - t0
        logger.error("local_api relay to %s failed: %s", service, exc, exc_info=True)
        _record_failed(error="unexpected_error", duration=duration, detail=str(exc))
        return {"error": f"Relay to {service} failed: {exc}"}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
