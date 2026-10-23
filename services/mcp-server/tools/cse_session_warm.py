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
    "lane_cse_stored_holder": False,
    "lane_cse_probe_error": True,
}

_ATTENDED_MESSAGES: dict[str, str] = {
    "no_attended_cse": "No mission-purpose attended CSE registered with bound chat_url",
    "ambiguous_attended": "Multiple mission-purpose attended candidates — operator must disambiguate",
    "attended_liveness_failed": "Sole attended candidate failed liveness on its registered port",
    "lane_cse_ambiguous": (
        "No single current CSE page for this lane; see data.reason and data.candidates"
    ),
    "lane_cse_none": "No live CSE page claims this lane",
    "lane_cse_stored_holder": (
        "Stored seat holder has no open claimed page; not a paste target"
    ),
    "lane_cse_probe_error": "Lane-current CDP probe failed; absence is unknown",
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
                return resp.json()
            if resp.status_code in {404, 409, 424} or (
                lane and resp.status_code == 503
            ):
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
                record(
                    "mcp.cse_session.resolve_attended",
                    code=code,
                    retryable=result["retryable"],
                )
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
    envelope: str = "free",
    predecessor_registration_id: str | None = None,
    successor_registration_id: str | None = None,
    successor_birth_id: str | None = None,
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
            "envelope": envelope,
            "predecessor_registration_id": predecessor_registration_id,
            "successor_registration_id": successor_registration_id,
            "successor_birth_id": successor_birth_id,
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


_MAESTRO_OBS_WAKE_PROMPT = (
    "TYPE: DIRECTIVE\n"
    "observability: owed_wake — unread milestone or restart completion pending on this lane.\n"
    "Action: read agent_bus unread for this house and continue the arc.\n"
)

# Castle operator-proxy lane — same parent_thread as cse_session(op=resolve_attended).
_MAESTRO_OPERATOR_PROXY_PARENT_THREAD = "12286"


def _attended_binding_from_resolve(resolved: dict[str, Any]) -> dict[str, str | None] | None:
    """Live paste target from relay_attended / resolve_attended, or None to retry."""
    if resolved.get("code"):
        return None
    current = resolved.get("current")
    if not isinstance(current, dict):
        return None
    chat_url = str(current.get("chat_url") or "").strip() or None
    registration_id = str(current.get("registration_id") or "").strip() or None
    execution_id = str(current.get("execution_id") or "").strip() or None
    if not any((chat_url, registration_id, execution_id)):
        return None
    return {
        "chat_url": chat_url,
        "registration_id": registration_id,
        "execution_id": execution_id,
    }


def dispatch_cse_holder_followup(*, holder: dict[str, Any]) -> bool:
    """One mechanical followup for a driving/dormant CSE holder (producer 1/2)."""
    _ = holder  # holder row may be superseded; wake target comes from resolve_attended.
    resolved = relay_attended(parent_thread=_MAESTRO_OPERATOR_PROXY_PARENT_THREAD)
    binding = _attended_binding_from_resolve(resolved)
    if binding is None:
        return False
    result = relay_followup(
        chat_url=binding["chat_url"],
        registration_id=binding["registration_id"],
        execution_id=binding["execution_id"],
        cdp_url=None,
        prompt_text=_MAESTRO_OBS_WAKE_PROMPT,
        prompt_uri=None,
        prompt_path=None,
        purpose="owed_wake",
        timeout_s=30.0,
        reattach=True,
        retain_lane=True,
        min_receipt="dom_paste",
        parent_thread=_MAESTRO_OPERATOR_PROXY_PARENT_THREAD,
        envelope="free",
    )
    return bool(result.get("ok")) or result.get("send_verified") is True


def dispatch_bridge_wake(
    *,
    lane_thread_id: str | None,
    context: dict[str, Any] | None = None,
) -> bool:
    """BRIDGE_WAKE path for :8 Cursor occupants when already wired on the lane."""
    import asyncio
    import concurrent.futures

    lane = (lane_thread_id or "").strip()
    if not lane:
        return False
    refs = (context or {}).get("turn_refs") or []
    turn_numbers = [
        int(ref["turn_number"])
        for ref in refs
        if isinstance(ref, dict) and ref.get("turn_number") is not None
    ]
    try:
        from cursor_bridge.wake_delivery import deliver_wake

        turn_arg: object = turn_numbers[0] if len(turn_numbers) == 1 else turn_numbers

        async def _deliver() -> dict[str, Any]:
            return await deliver_wake(
                display="8",
                app="cursor",
                thread=lane,
                turn=turn_arg if turn_numbers else 0,
            )

        def _run_coro() -> dict[str, Any]:
            return asyncio.run(_deliver())

        try:
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False
        if in_running_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(_run_coro).result(timeout=60.0)
        else:
            result = asyncio.run(_deliver())
        return bool(result.get("ok"))
    except Exception:
        return False
