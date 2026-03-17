"""Local API relay — HTTP passthrough into Docker internal network services.

Agents call `local_api(service, method, path, ...)` via dispatch. The MCP
server forwards the request to the named service inside the Docker network
and returns the parsed JSON response.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0

_SERVICES: dict[str, dict[str, str]] = {
    "journal-bridge": {
        "base_url": "http://journal-bridge:8200",
        "token_env": "BRIDGE_TOKEN",
    },
    "agent-bus": {
        "base_url": "http://agent-bus:8100",
        "token_env": "AGENT_BUS_TOKEN",
    },
    "cortex-api": {
        "base_url": "http://cortex-api:8300",
    },
}


def _relay(
    service: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Forward an HTTP request to an internal Docker network service.

    Module-level relay function used by both the ``local_api`` MCP tool
    and other internal callers (e.g. context tools routing through cortex-api).

    Returns:
        Parsed JSON response from the service, or ``{"error": "<message>"}``.
    """
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return {"error": f"Unsupported HTTP method: {method!r}"}

    svc_config = _SERVICES.get(service)
    if svc_config is None:
        return {
            "error": (f"Unknown service: {service!r}. Available: {sorted(_SERVICES)}")
        }

    base_url = svc_config["base_url"]
    url = f"{base_url}{path}"

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
    )

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            response = client.request(
                method,
                url,
                json=body,
                headers=headers,
            )
            duration = monotonic_now() - t0

            if response.status_code >= 400:
                _record_failed(
                    error="http_error",
                    status=response.status_code,
                    duration=duration,
                )
                return {
                    "error": f"HTTP {response.status_code}",
                    "status_code": response.status_code,
                    "body": response.text,
                }

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
                return {"text": response.text}

            record(
                "mcp.local.api.completed",
                service=service,
                method=method,
                path=path,
                status=response.status_code,
                duration_s=round(duration, 3),
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


def register_local_api_tools(mcp: FastMCP) -> None:
    """Register the local_api relay tool on the MCP server instance."""

    @mcp.tool()
    def local_api(
        service: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Forward an HTTP request to an internal Docker network service.

        Acts as a relay from MCP into services on the Docker bridge network
        that are not directly reachable from the internet.

        Services:
          journal-bridge — Journal Bridge API (port 8200)
          agent-bus      — Agent Bus API (port 8100)
          cortex-api     — Cortex Knowledge System API (port 8300)

        Args:
            service: Service name from the registry above.
            method: HTTP method — GET, POST, PUT, PATCH, or DELETE.
            path: Request path with optional query string, e.g. "/entries?limit=5".
            body: Optional JSON body for POST/PUT requests.
            token: Bearer token override. Falls back to the service's env var.

        Returns:
            Parsed JSON response from the service, or {"error": "<message>"}.
        """
        return _relay(service, method, path, body=body, token=token)
