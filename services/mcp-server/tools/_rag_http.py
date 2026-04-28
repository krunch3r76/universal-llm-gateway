"""Shared HTTP helpers for RAG MCP tools."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from mcp_events import record

logger = logging.getLogger(__name__)


def _rag_get(
    url_base: str,
    path: str,
    *,
    timeout: float,
    params: Any | None = None,
) -> dict[str, Any]:
    """GET from Stargate passthrough and return parsed JSON."""
    url = f"{url_base.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def _rag_post(
    url_base: str,
    path: str,
    body: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """POST JSON to Stargate passthrough and return parsed object payload."""
    url = f"{url_base.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def _rag_delete(
    url_base: str,
    path: str,
    *,
    timeout: float,
    params: Any | None = None,
) -> dict[str, Any]:
    """DELETE on Stargate passthrough and return parsed JSON object."""
    url = f"{url_base.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.delete(url, params=params)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def _handle_rag_call_error(
    exc: BaseException,
    *,
    endpoint_name: str,
) -> dict[str, str]:
    """Record/log RAG passthrough errors and return a user-facing error payload."""
    signal = f"mcp.rag.{endpoint_name}.failed"
    if isinstance(exc, httpx.ConnectError):
        logger.warning("RAG %s connection failed: %s", endpoint_name, exc)
        record(signal, error=str(exc))
        return {
            "error": (
                f"RAG {endpoint_name} endpoint not reachable. "
                "Ensure RAG is running and reachable through Stargate."
            )
        }
    if isinstance(exc, httpx.TimeoutException):
        logger.warning("RAG %s request timed out: %s", endpoint_name, exc)
        record(signal, error="timeout")
        return {"error": f"RAG {endpoint_name} request timed out."}
    if isinstance(exc, httpx.HTTPStatusError):
        logger.warning("RAG %s HTTP error: %s", endpoint_name, exc)
        record(signal, error=f"{exc.response.status_code}")
        return {
            "error": (
                f"RAG {endpoint_name} endpoint error: "
                f"{exc.response.status_code} {exc.response.reason_phrase}"
            )
        }
    if isinstance(exc, httpx.RequestError):
        logger.warning("RAG %s request error: %s", endpoint_name, exc)
        record(signal, error=str(exc))
        return {"error": f"RAG {endpoint_name} request failed: {exc}"}
    logger.warning("RAG %s invalid payload: %s", endpoint_name, exc)
    record(signal, error="invalid_payload")
    return {"error": f"RAG {endpoint_name} endpoint returned invalid payload."}
