"""POST git_integration_worker cursor-sdk dispatch after handoff thread creation."""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

import httpx
from agent_bus_store.close_on_read import CloseContract, append_close_on_read_marker
from agent_bus_store.disposition import append_bus_lifecycle_tags
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)


def _worker_read_timeout_s() -> float:
    """Friction 23001: read timeout on the worker admission POST.

    Env-configurable; default 60s. The worker now responds fast (baseline
    capture deferred off the request path), but headroom prevents
    false-negative 599s — which the caller may retry, duplicating execution
    (friction 23002) — if admission ever slows again.
    """
    raw = os.environ.get("CURSOR_SDK_WORKER_READ_TIMEOUT_S", "60")
    try:
        return float(raw)
    except ValueError:
        return 60.0


_WORKER_TIMEOUT = httpx.Timeout(
    connect=5.0, read=_worker_read_timeout_s(), write=10.0, pool=5.0
)


def resolve_close_contract_bus_lifecycle(
    close_contract: CloseContract | None,
) -> Literal["persistent", "ephemeral"] | None:
    """Map close-contract to lifecycle; ``lead`` reserves closure for adjudication."""
    if close_contract == "lead":
        return "persistent"
    return None


def assemble_cursor_sdk_generate_tags(
    base_tags: list[str],
    *,
    close_contract: CloseContract = "auto",
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
) -> list[str]:
    """Apply bus lifecycle + close-on-read marker for cursor-sdk generate threads."""
    effective_lifecycle: Literal["persistent", "ephemeral"] = (
        resolve_close_contract_bus_lifecycle(close_contract)
        or bus_lifecycle
        or "ephemeral"
    )
    tagged = append_bus_lifecycle_tags(base_tags, bus_lifecycle=effective_lifecycle)
    return append_close_on_read_marker(
        tagged,
        bus_lifecycle=effective_lifecycle,
        close_contract=close_contract,
    )


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
    injected_section = f"## Injected invariant bodies\n{resolution.block_md.strip()}\n"
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
    if isinstance(body, dict) and body.get("dispatch_id"):
        detail["dispatch_id"] = str(body["dispatch_id"])
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
    close_contract: CloseContract = "auto",
    dispatch_id: str | None = None,
    nest_under: str | None = None,
    lane: Literal["A", "B"] | None = None,
    workspace: str | None = None,
    refuse_if_lease_held: bool = False,
    hop_from: str | None = None,
    hop_seq: int | None = None,
    hop_reason: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/cursor/dispatch``; return structured ``(ok, detail)``.

    When ``dispatch_id`` is supplied (prepared-handle path), reuse it so
    retries share the ledger idempotency key. Legacy/unprepared callers
    still mint ``{request_id}-{uuid8}``.

    ``nest_under`` parks the named parent write-lease holder so this child can
    admit under limit=1 (PARK-RESTORE-DUAL).
    """
    effective_dispatch_id = dispatch_id or f"{request_id}-{uuid.uuid4().hex[:8]}"
    payload: dict[str, object] = {
        "thread_id": thread_id,
        "model": model,
        "packet_path": packet_path,
        "dispatch_id": effective_dispatch_id,
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
    if close_contract != "auto":
        payload["close_contract"] = close_contract
    if nest_under:
        payload["nest_under"] = nest_under
    if lane:
        payload["lane"] = lane
    if workspace:
        payload["workspace"] = workspace
    if refuse_if_lease_held:
        payload["refuse_if_lease_held"] = True
    if hop_from is not None and hop_seq is not None and hop_reason is not None:
        payload["hop_from"] = hop_from
        payload["hop_seq"] = hop_seq
        payload["hop_reason"] = hop_reason
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
        return False, _transport_failure_detail(
            dispatch_id=effective_dispatch_id, exc=exc
        )
    if resp.status_code in (200, 202):
        logger.info(
            "cursor-sdk worker admit: request_id=%s status=%s body=%s",
            request_id,
            resp.status_code,
            resp.text[:300],
        )
        detail = _parse_worker_success(resp)
        detail.setdefault("dispatch_id", effective_dispatch_id)
        return True, detail
    logger.warning(
        "cursor-sdk worker rejected dispatch: request_id=%s status=%s body=%s",
        request_id,
        resp.status_code,
        resp.text[:200],
    )
    return False, _parse_worker_error(resp, dispatch_id=effective_dispatch_id)


async def dispatch_cursor_sdk_worker_message(
    *,
    request_id: str,
    thread_id: str,
    model: str,
    message: str,
    execution_id: str,
    handoff_contract: str,
    caller_agent: str | None = None,
    model_knobs: dict[str, str] | None = None,
    read_only: bool = False,
    dispatch_id: str | None = None,
    nest_under: str | None = None,
    lane: Literal["A", "B"] | None = None,
    workspace: str | None = None,
    refuse_if_lease_held: bool = False,
    hop_from: str | None = None,
    hop_seq: int | None = None,
    hop_reason: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/cursor/dispatch`` with ``message`` (prompt= path)."""
    effective_dispatch_id = dispatch_id or f"{request_id}-{uuid.uuid4().hex[:8]}"
    payload = {
        "thread_id": thread_id,
        "model": model,
        "message": message,
        "dispatch_id": effective_dispatch_id,
        "execution_id": execution_id,
        "handoff_contract": handoff_contract,
    }
    if caller_agent is not None:
        payload["caller_agent"] = caller_agent
    if model_knobs:
        payload["model_knobs"] = model_knobs
    if read_only:
        payload["read_only"] = True
    if nest_under:
        payload["nest_under"] = nest_under
    if lane:
        payload["lane"] = lane
    if workspace:
        payload["workspace"] = workspace
    if refuse_if_lease_held:
        payload["refuse_if_lease_held"] = True
    if hop_from is not None and hop_seq is not None and hop_reason is not None:
        payload["hop_from"] = hop_from
        payload["hop_seq"] = hop_seq
        payload["hop_reason"] = hop_reason
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
        return False, _transport_failure_detail(
            dispatch_id=effective_dispatch_id, exc=exc
        )
    if resp.status_code in (200, 202):
        detail = _parse_worker_success(resp)
        detail.setdefault("dispatch_id", effective_dispatch_id)
        return True, detail
    return False, _parse_worker_error(resp, dispatch_id=effective_dispatch_id)


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
