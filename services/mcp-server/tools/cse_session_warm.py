"""Warm-paste and attended-resolve relays for cse_session.

Same satellite routes as the retired MCP project_ask followup/attended ops.
No Playwright or claude_bundles imports.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from mcp_events import record

# Satellite followup does not abort Playwright on body timeout_s. Equal MCP
# httpx vs satellite budgets race: paste_verified can fire after the tool
# returns "unreachable" (2026-08-20 wait-report followups).
HTTP_TIMEOUT_SLACK_S = 60.0


def http_client_timeout_s(satellite_timeout_s: float) -> float:
    """HTTP wait the MCP relay uses — satellite budget plus slack."""
    return float(satellite_timeout_s) + HTTP_TIMEOUT_SLACK_S


def transport_failure_payload(
    exc: BaseException, *, path: str, timeout_s: float
) -> dict[str, Any]:
    """Map httpx transport errors. Timeout ≠ unreachable; paste may still land."""
    if isinstance(exc, httpx.TimeoutException):
        record("mcp.cse_session.relay.failed", path=path, kind="timeout")
        return {
            "ok": False,
            "code": "cse_session_http_timeout",
            "error": (
                f"cse-session timed out after {timeout_s:.0f}s waiting for satellite"
            ),
            "retryable": True,
            "indeterminate": True,
        }
    record("mcp.cse_session.relay.failed", path=path, kind="unreachable")
    return {
        "ok": False,
        "error": f"cse-session unreachable: {exc}",
    }


_ATTENDED_RETRYABLE: dict[str, bool] = {
    "no_attended_cse": True,
    "ambiguous_attended": False,
    "attended_liveness_failed": True,
    "lane_cse_ambiguous": True,
    "lane_cse_none": True,
}

_ATTENDED_MESSAGES: dict[str, str] = {
    "no_attended_cse": "No mission-purpose attended CSE registered with bound chat_url",
    "ambiguous_attended": "Multiple mission-purpose attended candidates — operator must disambiguate",
    "attended_liveness_failed": "Sole attended candidate failed liveness on its registered port",
    "lane_cse_ambiguous": (
        "Several live CSE pages claim this lane and none is uniquely in flight"
    ),
    "lane_cse_none": "No live CSE page claims this lane",
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
    http_timeout = http_client_timeout_s(timeout_s)
    try:
        with httpx.Client(timeout=http_timeout) as client:
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
        return transport_failure_payload(exc, path=path, timeout_s=http_timeout)


def _normalize_cse_url(url: str | None) -> str:
    """Match ``claude_bundles.cse_url.normalize_cse_url`` without that import."""
    from urllib.parse import urlsplit, urlunsplit

    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _thread_last_associated(
    parent_thread: str, current_chat_url: str | None
) -> dict[str, Any] | None:
    """Last-associated CSE for the lane. Relay failure omits the field."""
    from tools.agent_bus._shared import relay

    try:
        result = relay("agent-bus", "GET", f"/threads/{parent_thread}/cse-current")
    except Exception:
        return None
    if not isinstance(result, dict) or result.get("error"):
        return None
    chat_url = result.get("cse_chat_url")
    current = _normalize_cse_url(current_chat_url)
    associated = _normalize_cse_url(chat_url if isinstance(chat_url, str) else None)
    return {
        "chat_url": chat_url,
        "association_id": result.get("association_id"),
        "matches_current": bool(current) and current == associated,
    }


def _attach_last_associated(
    payload: dict[str, Any],
    *,
    parent_thread: str | None,
    current_chat_url: str | None,
    inside_data: bool,
) -> dict[str, Any]:
    lane = (parent_thread or "").strip()
    if not lane:
        return payload
    associated = _thread_last_associated(lane, current_chat_url)
    if associated is None:
        return payload
    if inside_data:
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
            payload["data"] = data
        data["thread_last_associated"] = associated
    else:
        payload["thread_last_associated"] = associated
    return payload


def relay_attended(
    *, timeout_s: float = 30.0, parent_thread: str | None = None
) -> dict[str, Any]:
    """GET attended-operator with ProtocolError envelope on refusal codes."""
    base = _project_ask_url()
    if not base:
        return _unconfigured()
    path = "/v1/project-ask/attended-operator"
    url = f"{base.rstrip('/')}{path}"
    http_timeout = http_client_timeout_s(timeout_s)
    params = None
    lane = (parent_thread or "").strip()
    if lane:
        params = {"parent_thread": lane}
    try:
        with httpx.Client(timeout=http_timeout) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                body = resp.json()
                current = body.get("current") if isinstance(body, dict) else None
                current_url = (
                    current.get("chat_url") if isinstance(current, dict) else None
                )
                if isinstance(body, dict):
                    return _attach_last_associated(
                        body,
                        parent_thread=lane or None,
                        current_chat_url=current_url,
                        inside_data=False,
                    )
                return body
            if resp.status_code in {404, 409, 424}:
                body = resp.json()
                code = str(body.get("code") or "attended_resolve_failed")
                data = {k: v for k, v in body.items() if k != "code"}
                current = (
                    data.get("current")
                    if isinstance(data.get("current"), dict)
                    else None
                )
                current_url = (
                    current.get("chat_url") if isinstance(current, dict) else None
                )
                result = {
                    "code": code,
                    "message": _ATTENDED_MESSAGES.get(code, code),
                    "source": "gateway",
                    "retryable": _ATTENDED_RETRYABLE.get(code, False),
                    "data": data,
                }
                record(
                    "mcp.cse_session.resolve_attended",
                    code=code,
                    retryable=result["retryable"],
                )
                return _attach_last_associated(
                    result,
                    parent_thread=lane or None,
                    current_chat_url=current_url,
                    inside_data=True,
                )
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
        return transport_failure_payload(exc, path=path, timeout_s=http_timeout)


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
    parent_thread: str | None = None,
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
            "parent_thread": parent_thread,
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
