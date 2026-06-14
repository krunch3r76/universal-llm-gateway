"""POST git_integration_worker cursor-sdk dispatch after handoff thread creation."""

from __future__ import annotations

import os
import uuid

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_WORKER_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
_WORKER_DISPATCH_FAILED = "worker_dispatch: failed"


def worker_base_url() -> str:
    host = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
    port = os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")
    return f"http://{host}:{port}"


def derive_cursor_sdk_prompt_preamble(
    *,
    handoff_contract: str,
    pointer: str,
) -> str | None:
    """Single imperative paragraph for implement dispatches (1b from pointer)."""
    if handoff_contract != "implement":
        return None
    for line in pointer.splitlines():
        stripped = line.strip()
        if stripped.startswith("Contract:"):
            return (
                f"{stripped} Execute this task NOW using your tools. Make the "
                "code/file changes the packet specifies. If you are blocked, "
                "reply with `status: blocked` and the specific reason. Do NOT "
                "reply with an acknowledgement-only message."
            )
    return None


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
) -> tuple[bool, str | None]:
    """POST ``/api/v1/cursor/dispatch``; return ``(ok, warning)``."""
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
        return False, _WORKER_DISPATCH_FAILED
    if resp.status_code >= 400:
        logger.warning(
            "cursor-sdk worker rejected dispatch: request_id=%s status=%s body=%s",
            request_id,
            resp.status_code,
            resp.text[:200],
        )
        return False, _WORKER_DISPATCH_FAILED
    return True, None


async def dispatch_cursor_sdk_worker_message(
    *,
    request_id: str,
    thread_id: str,
    model: str,
    message: str,
    execution_id: str,
    caller_agent: str | None = None,
) -> tuple[bool, str | None]:
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
        return False, _WORKER_DISPATCH_FAILED
    if resp.status_code >= 400:
        logger.warning(
            "cursor-sdk worker rejected message dispatch: request_id=%s status=%s",
            request_id,
            resp.status_code,
        )
        return False, _WORKER_DISPATCH_FAILED
    return True, None


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
