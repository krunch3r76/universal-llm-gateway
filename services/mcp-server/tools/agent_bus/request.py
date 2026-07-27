"""agent_bus ``request`` — life-callable Cursor Auto admit channel.

Writes a send-equivalent turn (injecting ``lane:cursor-auto``), probes a live
Auto handler, enqueues when armed, and returns ``{thread, turn, handler_status,
poll_hint}``. Distinct from ``send`` and from ``lane:life-to-code``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .send import _send_dispatch

_LANE_TAG = "lane:cursor-auto"
_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_SUGGESTED_INTERVAL_S = 2
_MAX_EXPECTED_LATENCY_S = 120
_AUTO_API_PREFIX = "/api/v1/git/cursor-auto"


def _worker_base_url() -> str:
    """Resolve Auto worker base URL for mcp→host reachability.

    Prefer ``GIT_INTEGRATION_WORKER_URL`` when set. Otherwise, from the mcp
    container, use ``STARGATE_URL`` so enqueue/liveness ride the existing
    ``/api/v1/git/*`` host-side proxy (worker binds 127.0.0.1 — unreachable
    via host.docker.internal). Host-local callers fall back to loopback.
    """
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


def _auto_url(path: str, *, base_url: str | None = None) -> str:
    base = (base_url or _worker_base_url()).rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{_AUTO_API_PREFIX}{suffix}"


def _merge_lane_tags(tags: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for t in list(tags or []) + [_LANE_TAG]:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def probe_auto_liveness(
    *,
    base_url: str | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Probe git_integration_worker Auto liveness (arm predicate).

    Returns ``{live: bool, ...}``. Transport failure ⇒ ``live=False`` (F1:
    never claim armed without a live handler).
    """
    url = _auto_url("/liveness", base_url=base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return {
                "live": False,
                "reason": "liveness_http_error",
                "status_code": resp.status_code,
            }
        data = resp.json()
        return {
            "live": bool(data.get("live")),
            "liveness": data,
            "reason": "ok" if data.get("live") else "no_live_handler",
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {"live": False, "reason": "liveness_unreachable", "error": str(exc)}


def enqueue_auto_job(
    *,
    thread_id: str,
    turn_number: int,
    subject: str,
    body: str,
    from_agent: str,
    to_agent: str,
    desired_model: str,
    desired_effort: str,
    contract: str,
    require_attended: bool = False,
    base_url: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST admit-on-request enqueue to the Auto worker."""
    url = _auto_url("/enqueue", base_url=base_url)
    payload = {
        "thread_id": str(thread_id),
        "turn_number": int(turn_number),
        "subject": subject,
        "body": body,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "desired_model": desired_model,
        "desired_effort": desired_effort,
        "contract": contract,
        "require_attended": bool(require_attended),
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            return {
                "ok": True,
                "handler_status": "auto-admit-armed",
                "enqueue": data,
            }
        return {
            "ok": False,
            "handler_status": data.get("handler_status", "no-auto-handler"),
            "enqueue": data,
            "status_code": resp.status_code,
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "handler_status": "no-auto-handler",
            "reason": "enqueue_unreachable",
            "error": str(exc),
        }


def _build_poll_hint(*, thread_id: str, after_turn: int) -> dict[str, Any]:
    return {
        "tool": "wait",
        "arguments_json": {
            "thread": str(thread_id),
            "after_turn": after_turn,
            "completion": "status:done",
            "wait_seconds": 0,
        },
        "suggested_interval_s": _SUGGESTED_INTERVAL_S,
        "max_expected_latency_s": _MAX_EXPECTED_LATENCY_S,
        "alternate_completions": [
            "status:failed",
            "status:needs-attended",
        ],
    }


def _request_impl(
    *,
    new_slug: str | None,
    thread: str | None,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    tags: list[str] | None,
    sidecar_content: str | None,
    sidecar_slug: str | None,
    desired_model: str,
    desired_effort: str,
    contract: str,
    require_attended: bool,
    after_turn: int,
) -> dict[str, Any]:
    """Write turn via send path, then arm/enqueue Auto when live."""
    merged_tags = _merge_lane_tags(tags)
    send_result = _send_dispatch(
        new_slug=new_slug,
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        tags=merged_tags,
        after_turn=after_turn,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
    )
    if isinstance(send_result, dict) and "error" in send_result:
        record("mcp.agentbus.request.failed", error=str(send_result.get("error")))
        return send_result

    thread_obj = send_result.get("thread") or {}
    turn_obj = send_result.get("turn") or {}
    thread_id = str(thread_obj.get("id") or thread or "")
    turn_number = int(turn_obj.get("turn_number") or 1)
    # Hoist send-path sidecar fields — callers (esp. Cowork) check top-level
    # sidecar_uri; dropping them made successful writes look like failures
    # (a:26439 item 5 / todo:agent-bus-sidecar-uri-null-on-write).
    sidecar_uri = send_result.get("sidecar_uri")
    sidecar_sha256 = send_result.get("sidecar_sha256")
    if sidecar_uri is None and isinstance(turn_obj, dict):
        sidecar_uri = turn_obj.get("sidecar_uri")
    if sidecar_sha256 is None and isinstance(turn_obj, dict):
        sidecar_sha256 = turn_obj.get("sidecar_sha256")

    liveness = probe_auto_liveness()
    if not liveness.get("live"):
        record(
            "mcp.agentbus.request.degraded",
            thread=thread_id,
            turn_number=turn_number,
            reason=str(liveness.get("reason", "no_live_handler")),
        )
        return {
            "thread": thread_obj,
            "turn": turn_obj,
            "handler_status": "no-auto-handler",
            "poll_hint": _build_poll_hint(
                thread_id=thread_id, after_turn=turn_number
            ),
            "liveness": liveness,
            "tags": merged_tags,
            "sidecar_uri": sidecar_uri,
            "sidecar_sha256": sidecar_sha256,
        }

    enq = enqueue_auto_job(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=subject,
        body=body,
        from_agent=from_agent,
        to_agent=to,
        desired_model=desired_model,
        desired_effort=desired_effort,
        contract=contract,
        require_attended=require_attended,
    )
    handler_status = (
        "auto-admit-armed" if enq.get("ok") else "no-auto-handler"
    )
    record(
        "mcp.agentbus.request.posted",
        thread=thread_id,
        turn_number=turn_number,
        handler_status=handler_status,
        desired_model=desired_model,
        contract=contract,
    )
    return {
        "thread": thread_obj,
        "turn": turn_obj,
        "handler_status": handler_status,
        "poll_hint": _build_poll_hint(thread_id=thread_id, after_turn=turn_number),
        "enqueue": enq,
        "tags": merged_tags,
        "sidecar_uri": sidecar_uri,
        "sidecar_sha256": sidecar_sha256,
    }


def _request_dispatch(
    *,
    new_slug: str | None = None,
    thread: str | int | None = None,
    to: str = "cursor",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    tags: list[str] | None = None,
    sidecar_content: str | None = None,
    sidecar_slug: str | None = None,
    desired_model: str = "auto",
    desired_effort: str = "medium",
    contract: str = "answer",
    require_attended: bool = False,
    after_turn: int = 0,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.request``.

    ``require_attended`` (default false): when true, Auto refuses unattended
    nested dispatch and in-seat substitute — terminal ``status:needs-attended``
    with ``reason=operator_require_attended``. Body field ``require_attended:
    true`` or ``executor_bind: attended`` ORs with the wire param.
    """
    if isinstance(thread, int):
        thread = str(thread)

    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err

    has_new_slug = new_slug is not None
    has_thread = bool(thread)
    if has_new_slug == has_thread:
        record("mcp.agentbus.request.rejected", reason="xor")
        return {
            "error": (
                "request: exactly one of thread or new_slug is required"
            ),
            "reason": "request_xor_violation",
        }
    if not subject or not body:
        return {
            "error": "request: subject and body are required",
            "missing_fields": [
                f for f, v in (("subject", subject), ("body", body)) if not v
            ],
        }
    if to and to != "cursor":
        return {
            "error": "request: v0 only supports to='cursor'",
            "reason": "request_to_unsupported",
            "provided": to,
        }

    return _request_impl(
        new_slug=new_slug,
        thread=thread,
        to=to or "cursor",
        subject=subject,
        body=body,
        from_agent=from_agent,
        tags=tags,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
        desired_model=desired_model or "auto",
        desired_effort=desired_effort or "medium",
        contract=contract or "answer",
        require_attended=bool(require_attended),
        after_turn=after_turn,
    )
