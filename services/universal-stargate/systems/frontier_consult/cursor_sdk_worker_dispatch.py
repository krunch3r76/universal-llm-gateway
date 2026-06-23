"""POST git_integration_worker cursor-sdk dispatch after handoff thread creation."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_WORKER_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


def _classify_transport_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.DecodingError)):
        return "bad_response"
    if isinstance(exc, httpx.ConnectError):
        err_str = str(exc).lower()
        if any(
            token in err_str
            for token in ("getaddrinfo", "name or service not known", "nodename")
        ):
            return "dns"
        if any(token in err_str for token in ("ssl", "tls", "certificate")):
            return "tls"
        return "connect_refused"
    return "unknown"


def _transport_failure_detail(
    *,
    dispatch_id: str,
    exc: httpx.HTTPError,
) -> dict[str, Any]:
    worker_code = "CURSOR_WORKER_UNREACHABLE"
    return {
        "status_code": 599,
        "http_status": None,
        "code": worker_code,
        "worker_error_code": worker_code,
        "message": str(exc),
        "failure_layer": "transport",
        "transport_error_kind": _classify_transport_error(exc),
        "dispatch_id": dispatch_id,
        "detail_summary": str(exc),
    }


def worker_base_url() -> str:
    host = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
    port = os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")
    return f"http://{host}:{port}"


def derive_cursor_sdk_prompt_preamble(
    *,
    handoff_contract: str,
    pointer: str,
    packet_text: str = "",
) -> str | None:
    """Single imperative paragraph for implement dispatches (1b from pointer)."""
    preamble: str | None = None
    if handoff_contract == "implement":
        for line in pointer.splitlines():
            stripped = line.strip()
            if stripped.startswith("Contract:"):
                preamble = (
                    f"{stripped} Execute this task NOW using your tools. Make the "
                    "code/file changes the packet specifies. If you are blocked, "
                    "reply with `status: blocked` and the specific reason. Do NOT "
                    "reply with an acknowledgement-only message."
                )
                break
    if not packet_text:
        return preamble
    from agent_seat.inject_registry import (
        parse_packet_invariant_skill_ids,
        resolve_injected_bodies,
    )

    packet_ids = parse_packet_invariant_skill_ids(packet_text)
    if not packet_ids:
        return preamble
    resolution = resolve_injected_bodies(
        "",
        role="cursor-sdk",
        platform="cursor",
        inject_profile="dispatch",
        packet_invariant_ids=packet_ids,
        budget_bytes=None,
    )
    if not resolution.block_md:
        return preamble
    injected_section = (
        "## Injected invariant bodies\n" f"{resolution.block_md.strip()}\n"
    )
    if preamble:
        return f"{injected_section}\n{preamble}"
    return injected_section.strip()


def _parse_worker_error(resp: httpx.Response, *, dispatch_id: str) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {"message": resp.text[:500]}
    if not isinstance(body, dict):
        body = {"message": str(body)}
    code = body.get("code") or "CURSOR_DISPATCH_REJECTED"
    message = body.get("message") or resp.text[:500]
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    blocking = data.get("blocking_dispatch_id")
    return {
        "status_code": resp.status_code,
        "http_status": resp.status_code,
        "code": str(code),
        "worker_error_code": str(code),
        "message": str(message),
        "blocking_dispatch_id": blocking,
        "failure_layer": "http",
        "dispatch_id": dispatch_id,
        "detail_summary": str(message),
    }


def _parse_worker_success(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    detail: dict[str, Any] = {
        "status_code": resp.status_code,
        "ticket": body,
    }
    if resp.status_code == 202 or body.get("status") == "queued":
        detail["queued"] = True
    return detail


async def dispatch_cursor_sdk_worker(
    *,
    request_id: str,
    thread_id: str,
    model: str,
    execution_id: str,
    packet_path: str,
    handoff_contract: str,
    caller_agent: str | None = None,
    prompt_preamble: str | None = None,
    model_knobs: dict[str, str] | None = None,
    read_only: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/cursor/dispatch``; return structured ``(ok, detail)``."""
    dispatch_id = f"{request_id}-{uuid.uuid4().hex[:8]}"
    payload: dict[str, object] = {
        "thread_id": thread_id,
        "model": model,
        "packet_path": packet_path,
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
        "handoff_contract": handoff_contract,
        "prompt_preamble": prompt_preamble,
    }
    if caller_agent is not None:
        payload["caller_agent"] = caller_agent
    if model_knobs:
        payload["model_knobs"] = model_knobs
    if read_only:
        payload["read_only"] = True
    try:
        async with make_async_client(
            worker_base_url(), timeout=_WORKER_TIMEOUT
        ) as client:
            resp = await client.post("/api/v1/cursor/dispatch", json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk worker unreachable: request_id=%s thread=%s err=%s",
            request_id,
            thread_id,
            exc,
        )
        return False, _transport_failure_detail(dispatch_id=dispatch_id, exc=exc)
    if resp.status_code in (200, 202):
        logger.info(
            "cursor-sdk worker admit: request_id=%s status=%s body=%s",
            request_id,
            resp.status_code,
            resp.text[:300],
        )
        return True, _parse_worker_success(resp)
    logger.warning(
        "cursor-sdk worker rejected dispatch: request_id=%s status=%s body=%s",
        request_id,
        resp.status_code,
        resp.text[:200],
    )
    return False, _parse_worker_error(resp, dispatch_id=dispatch_id)


async def dispatch_cursor_sdk_worker_message(
    *,
    request_id: str,
    thread_id: str,
    model: str,
    message: str,
    execution_id: str,
    caller_agent: str | None = None,
    model_knobs: dict[str, str] | None = None,
    read_only: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/cursor/dispatch`` with ``message`` (consult path)."""
    dispatch_id = f"{request_id}-{uuid.uuid4().hex[:8]}"
    payload = {
        "thread_id": thread_id,
        "model": model,
        "message": message,
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
    }
    if caller_agent is not None:
        payload["caller_agent"] = caller_agent
    if model_knobs:
        payload["model_knobs"] = model_knobs
    if read_only:
        payload["read_only"] = True
    try:
        async with make_async_client(
            worker_base_url(), timeout=_WORKER_TIMEOUT
        ) as client:
            resp = await client.post("/api/v1/cursor/dispatch", json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk worker unreachable (message): request_id=%s err=%s",
            request_id,
            exc,
        )
        return False, _transport_failure_detail(dispatch_id=dispatch_id, exc=exc)
    if resp.status_code in (200, 202):
        return True, _parse_worker_success(resp)
    return False, _parse_worker_error(resp, dispatch_id=dispatch_id)


async def post_worker_failure_turn(
    *, thread_id: str, request_id: str, to_agent: str = "cursor-sdk"
) -> None:
    """Best-effort failure turn when worker dispatch does not admit."""
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        return
    payload = {
        "thread": thread_id,
        "from": "dispatch",
        "to": to_agent,
        "subject": f"cursor-sdk worker dispatch failed ({request_id})",
        "body": (
            "Automated cursor-sdk worker dispatch failed (worker unreachable or "
            "rejected admission). Re-dispatch manually or restart "
            "git_integration_worker."
        ),
        "status": "open",
        "after_turn": 0,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            await client.post("/turns", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "cursor-sdk failure turn not posted: thread=%s err=%s",
            thread_id,
            exc,
        )
