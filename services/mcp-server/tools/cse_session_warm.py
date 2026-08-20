"""Warm-paste and attended-resolve relays for cse_session.

Same satellite routes as the retired MCP project_ask followup/attended ops.
No Playwright or claude_bundles imports.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from mcp_events import record

_ATTENDED_RETRYABLE: dict[str, bool] = {
    "no_attended_cse": True,
    "ambiguous_attended": False,
    "attended_liveness_failed": True,
}

_ATTENDED_MESSAGES: dict[str, str] = {
    "no_attended_cse": "No mission-purpose attended CSE registered with bound chat_url",
    "ambiguous_attended": "Multiple mission-purpose attended candidates — operator must disambiguate",
    "attended_liveness_failed": "Sole attended candidate failed liveness on its registered port",
}


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def _unconfigured() -> dict[str, Any]:
    return {
        "error": (
            "PROJECT_ASK_URL not configured. Start the cdp-ask satellite on "
            "Jupiter and set PROJECT_ASK_URL=http://HOST:PORT in the MCP "
            "server environment."
        )
    }


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    base = _project_ask_url()
    if not base:
        return _unconfigured()
    url = f"{base.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(method, url, json=json_body)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}
    except httpx.HTTPStatusError as exc:
        record(
            "mcp.cse_session.relay.failed",
            path=path,
            kind="http_status",
            status=exc.response.status_code,
        )
        return {
            "error": f"cse-session HTTP {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "detail": exc.response.text[:400],
        }
    except httpx.RequestError as exc:
        record("mcp.cse_session.relay.failed", path=path, kind="unreachable")
        return {"error": f"cse-session unreachable: {exc}"}


def relay_attended(*, timeout_s: float = 30.0) -> dict[str, Any]:
    """GET attended-operator with ProtocolError envelope on refusal codes."""
    base = _project_ask_url()
    if not base:
        return _unconfigured()
    path = "/v1/project-ask/attended-operator"
    url = f"{base.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in {404, 409, 424}:
                body = resp.json()
                code = str(body.get("code") or "attended_resolve_failed")
                data = {k: v for k, v in body.items() if k != "code"}
                result = {
                    "code": code,
                    "message": _ATTENDED_MESSAGES.get(code, code),
                    "source": "gateway",
                    "retryable": _ATTENDED_RETRYABLE.get(code, False),
                    "data": data,
                }
                record("mcp.cse_session.resolve_attended", code=code, retryable=result["retryable"])
                return result
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        record(
            "mcp.cse_session.relay.failed",
            path=path,
            kind="http_status",
            status=exc.response.status_code,
        )
        return {
            "error": f"cse-session HTTP {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "detail": exc.response.text[:400],
        }
    except httpx.RequestError as exc:
        record("mcp.cse_session.relay.failed", path=path, kind="unreachable")
        return {"error": f"cse-session unreachable: {exc}"}


def relay_followup(
    *,
    chat_url: str | None,
    registration_id: str | None,
    execution_id: str | None,
    cdp_url: str | None,
    prompt_text: str | None,
    prompt_uri: str | None,
    prompt_path: str | None,
    purpose: str,
    timeout_s: float,
    reattach: bool,
    retain_lane: bool,
    min_receipt: Literal["dom_paste", "dom_committed", "human_visible"],
) -> dict[str, Any]:
    """POST warm paste to ``/v1/project-ask/followups``."""
    if not any(
        [
            (prompt_text or "").strip(),
            (prompt_uri or "").strip(),
            (prompt_path or "").strip(),
        ]
    ):
        return {"ok": False, "error": "no_prompt"}
    body = {
        k: v
        for k, v in {
            "chat_url": chat_url,
            "registration_id": registration_id,
            "execution_id": execution_id,
            "cdp_url": cdp_url,
            "purpose": purpose if purpose != "ask" else None,
            "prompt_text": prompt_text,
            "prompt_uri": prompt_uri,
            "prompt_path": prompt_path,
            "timeout_s": int(timeout_s),
            "reattach": reattach,
            "retain_lane": retain_lane,
            "min_receipt": min_receipt if min_receipt != "dom_paste" else None,
        }.items()
        if v is not None and v != "" and v is not False
    }
    result = _relay(
        "POST",
        "/v1/project-ask/followups",
        json_body=body,
        timeout_s=timeout_s,
    )
    resolution_path = (
        "chat_url"
        if body.get("chat_url")
        else "registration_id"
        if body.get("registration_id")
        else "execution_id"
        if body.get("execution_id")
        else "attended_resolver"
    )
    record(
        "mcp.cse_session.followup",
        ok=result.get("ok"),
        error=result.get("error"),
        registration_id=result.get("registration_id"),
        send_verified=result.get("send_verified"),
        streaming_at_paste=result.get("streaming_at_paste"),
        resolution_path=resolution_path,
        lane_created=result.get("lane_created"),
        receipt=result.get("receipt"),
    )
    return result
