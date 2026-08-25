"""Shared httpx relay to the Jupiter CDP-ask satellite."""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp_events import record

from tools.cse_session_warm import http_client_timeout_s, transport_failure_payload


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
    failure_signal: str | None = None,
    error_prefix: str = "relay",
) -> dict[str, Any]:
    base = _project_ask_url()
    if not base:
        return {
            "error": (
                "PROJECT_ASK_URL not configured. Start the cdp-ask satellite on "
                "Jupiter and set PROJECT_ASK_URL=http://HOST:PORT in the MCP "
                "server environment."
            )
        }
    url = f"{base.rstrip('/')}{path}"
    http_timeout = http_client_timeout_s(timeout_s)
    try:
        with httpx.Client(timeout=http_timeout) as client:
            resp = client.request(method, url, json=json_body, params=params)
            if resp.status_code in {403, 404, 409, 424}:
                if resp.content:
                    body = resp.json()
                    code = str(body.get("code") or body.get("detail") or "protocol_error")
                    return {
                        "code": code,
                        "message": str(body.get("message") or code),
                        "source": "gateway",
                        "retryable": bool(body.get("retryable", False)),
                        "data": {k: v for k, v in body.items() if k not in {"code", "message"}},
                        "ok": False,
                    }
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}
    except httpx.HTTPStatusError as exc:
        if failure_signal:
            record(
                failure_signal,
                path=path,
                kind="http_status",
                status=exc.response.status_code,
            )
        detail = exc.response.text[:400]
        return {
            "error": f"{error_prefix} HTTP {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "detail": detail,
        }
    except httpx.RequestError as exc:
        return transport_failure_payload(exc, path=path, timeout_s=http_timeout)
