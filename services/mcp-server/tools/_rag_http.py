"""Shared HTTP helpers for RAG MCP tools."""

from __future__ import annotations

from typing import Any, cast

import httpx
from mcp_events import record
from transport_utils import make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_RAG_FAILED_SIGNAL = "mcp.rag.endpoint.failed"


def rag_get(
    url_base: str,
    path: str,
    *,
    timeout: float,
    params: Any | None = None,
) -> dict[str, Any]:
    """GET from Stargate passthrough and return parsed JSON."""
    with make_sync_client(url_base, timeout=timeout) as client:
        resp = client.get(f"/{path.lstrip('/')}", params=params)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def rag_post(
    url_base: str,
    path: str,
    body: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """POST JSON to Stargate passthrough and return parsed object payload."""
    with make_sync_client(url_base, timeout=timeout) as client:
        resp = client.post(f"/{path.lstrip('/')}", json=body)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def rag_delete(
    url_base: str,
    path: str,
    *,
    timeout: float,
    params: Any | None = None,
) -> dict[str, Any]:
    """DELETE on Stargate passthrough and return parsed JSON object."""
    with make_sync_client(url_base, timeout=timeout) as client:
        resp = client.delete(f"/{path.lstrip('/')}", params=params)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def handle_rag_call_error(
    exc: Exception,
    *,
    endpoint_name: str,
) -> dict[str, str]:
    """Record/log RAG passthrough errors and return a user-facing error payload."""
    if isinstance(exc, httpx.ConnectError):
        logger.warning("RAG %s connection failed: %s", endpoint_name, exc)
        record(_RAG_FAILED_SIGNAL, endpoint=endpoint_name, error=str(exc))
        return {
            "error": (
                f"RAG {endpoint_name} endpoint not reachable. "
                "Ensure RAG is running and reachable through Stargate."
            )
        }
    if isinstance(exc, httpx.TimeoutException):
        logger.warning("RAG %s request timed out: %s", endpoint_name, exc)
        record(_RAG_FAILED_SIGNAL, endpoint=endpoint_name, error="timeout")
        return {"error": f"RAG {endpoint_name} request timed out."}
    if isinstance(exc, httpx.HTTPStatusError):
        logger.warning("RAG %s HTTP error: %s", endpoint_name, exc)
        record(
            _RAG_FAILED_SIGNAL,
            endpoint=endpoint_name,
            error=f"{exc.response.status_code}",
        )
        return {
            "error": (
                f"RAG {endpoint_name} endpoint error: "
                f"{exc.response.status_code} {exc.response.reason_phrase}"
            )
        }
    if isinstance(exc, httpx.RequestError):
        logger.warning("RAG %s request error: %s", endpoint_name, exc)
        record(_RAG_FAILED_SIGNAL, endpoint=endpoint_name, error=str(exc))
        return {"error": f"RAG {endpoint_name} request failed: {exc}"}
    logger.warning("RAG %s invalid payload: %s", endpoint_name, exc)
    record(_RAG_FAILED_SIGNAL, endpoint=endpoint_name, error="invalid_payload")
    return {"error": f"RAG {endpoint_name} endpoint returned invalid payload."}
