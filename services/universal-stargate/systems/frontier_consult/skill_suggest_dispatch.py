"""Block-await skill-suggest dispatch — worker-hop primary, direct suggest fallback."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Literal

import httpx
from cortex_store.seat_applicability import canonical_seat_or_422
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from transport_utils import DEFAULT_AGENT_BUS_URL, DEFAULT_CORTEX_URL, make_async_client
from universal_logging import get_logger

from .admission import FrontierEndpointError
from .cursor_sdk_generate import CURSOR_SDK_REPLY_SEAT, dispatch_cursor_sdk_generate
from .events import (
    FrontierSkillSuggestDispatchCompleted,
    FrontierSkillSuggestDispatchDegraded,
)
from .handoff import _workspaces_root
from .skill_suggest_dispatch_helpers import (
    build_worker_message,
    parse_envelope_from_closeout,
)

logger = get_logger(__name__)

skills_router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

_CONTEXT_MAX = 16384
_WAIT_CHUNK_SECONDS = 60.0
_CORTEX_TIMEOUT = 30.0


class SkillSuggestDispatchRequest(BaseModel):
    model_config = {"extra": "forbid"}

    agent: str
    loaded: list[str]
    conversation_context: str | None = None
    limit: int = Field(default=8, ge=1, le=25)
    worker_timeout_seconds: int = Field(default=120, ge=1, le=300)


class SkillSuggestDispatchResponse(BaseModel):
    agent: str
    suggestions: list[dict[str, Any]]
    count: int
    omitted: list[Any]
    degraded_skills: list[Any]
    loaded_echo: list[Any]
    seat_preloaded: list[Any]
    ranker_status: str
    degraded: bool
    degraded_reason: str | None = None
    route: Literal["worker", "fallback"]
    dispatch_execution_id: str | None = None
    dispatch_durable: bool | None = None


def _publish_event(event: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event)
    except Exception:
        return


def _agent_bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _validate_loaded(loaded: Any) -> None:
    if not isinstance(loaded, list):
        raise HTTPException(
            status_code=422,
            detail={"code": "loaded_invalid", "message": "loaded must be a list"},
        )
    for item in loaded:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "loaded_invalid",
                    "message": "loaded elements must be strings",
                },
            )


def _canonicalize_agent(agent: str) -> str:
    if not agent or not str(agent).strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "agent_required", "message": "agent is required"},
        )
    return canonical_seat_or_422(str(agent).strip())


async def await_worker_reply(
    *,
    thread_id: str,
    worker_timeout_seconds: int,
    after_turn: int = 1,
) -> str | None:
    """Poll agent-bus wait until a cursor-sdk closeout turn appears or budget spent."""
    headers = _agent_bus_headers()
    deadline = time.monotonic() + worker_timeout_seconds
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=70.0) as client:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            wait_s = min(_WAIT_CHUNK_SECONDS, remaining)
            if wait_s <= 0:
                break
            try:
                resp = await client.get(
                    f"/threads/{thread_id}/wait",
                    params={
                        "after_turn": after_turn,
                        "wait": wait_s,
                        "completion": "first_reply_from",
                        "from_agent": CURSOR_SDK_REPLY_SEAT,
                    },
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                logger.warning("skill_suggest_dispatch wait transport error: %s", exc)
                await asyncio.sleep(1.0)
                continue
            if resp.status_code >= 400:
                logger.warning(
                    "skill_suggest_dispatch wait rejected: status=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            snap = resp.json()
            if not snap.get("complete"):
                continue
            reply_turn = snap.get("qualifying_reply_turn")
            if not isinstance(reply_turn, int):
                return None
            turn_resp = await client.get(
                "/turns/by-number",
                params={"thread": thread_id, "turn_number": reply_turn},
                headers=headers,
            )
            if turn_resp.status_code >= 400:
                return None
            body = turn_resp.json().get("body")
            return body if isinstance(body, str) else None
    return None


async def run_fallback(
    *,
    agent: str,
    loaded: list[str],
    conversation_context: str | None,
    limit: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent": agent,
        "loaded": loaded,
        "limit": limit,
    }
    if conversation_context is not None:
        payload["conversation_context"] = conversation_context
    async with make_async_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
        resp = await client.post(
            "/skills/suggest",
            json=payload,
            headers={"X-Cortex-Transport": "stargate"},
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    result: dict[str, Any] = resp.json()
    result["route"] = "fallback"
    result["dispatch_execution_id"] = None
    return result


async def dispatch_skill_suggest(
    *,
    request_id: str,
    body: SkillSuggestDispatchRequest,
) -> dict[str, Any]:
    _validate_loaded(body.loaded)
    if (
        body.conversation_context is not None
        and len(body.conversation_context) > _CONTEXT_MAX
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "context_too_large",
                "message": f"conversation_context exceeds {_CONTEXT_MAX} characters",
            },
        )
    canonical_agent = _canonicalize_agent(body.agent)
    workspaces_root = _workspaces_root().resolve()
    message = build_worker_message(
        loaded=body.loaded,
        conversation_context=body.conversation_context,
        agent=canonical_agent,
        limit=body.limit,
    )
    t0 = time.monotonic()
    execution_id: str | None = None
    try:
        dispatch_result = await dispatch_cursor_sdk_generate(
            request_id=request_id,
            role="cursor-sdk",
            model=None,
            subject=f"skill-suggest dispatch — {request_id[:8]}",
            caller_agent=canonical_agent,
            contract="pure-mechanical",
            packet_path=None,
            message_text=message,
        )
    except FrontierEndpointError:
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason="dispatch_admission_rejected",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return result

    execution_id = str(dispatch_result.get("execution_id") or "")
    thread_id = str(dispatch_result.get("thread_id") or "")
    result_handle = dispatch_result.get("result_handle")
    dispatch_durable: bool | None = None
    if isinstance(result_handle, dict):
        durable_val = result_handle.get("durable")
        if isinstance(durable_val, bool):
            dispatch_durable = durable_val
    if not thread_id:
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason="missing_thread_id",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return result

    closeout_body = await await_worker_reply(
        thread_id=thread_id,
        worker_timeout_seconds=body.worker_timeout_seconds,
    )
    degraded_reason: str | None = None
    envelope: dict[str, Any] | None = None
    if closeout_body:
        envelope = parse_envelope_from_closeout(
            closeout_body,
            canonical_agent=canonical_agent,
            workspaces_root=workspaces_root,
        )
    if envelope is None:
        if closeout_body is None:
            degraded_reason = "worker_timeout"
        else:
            degraded_reason = "parse_or_sidecar_failure"
        result = await run_fallback(
            agent=canonical_agent,
            loaded=body.loaded,
            conversation_context=body.conversation_context,
            limit=body.limit,
        )
        _publish_event(
            FrontierSkillSuggestDispatchDegraded(
                request_id=request_id,
                agent=canonical_agent,
                route="fallback",
                reason=degraded_reason,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        )
        return result

    envelope["route"] = "worker"
    envelope["dispatch_execution_id"] = execution_id or None
    envelope["dispatch_durable"] = dispatch_durable
    _publish_event(
        FrontierSkillSuggestDispatchCompleted(
            request_id=request_id,
            agent=canonical_agent,
            route="worker",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    )
    return envelope


@skills_router.post(
    "/suggest-dispatch",
    status_code=200,
    response_model=SkillSuggestDispatchResponse,
    summary="Dispatch skill_suggest via cursor-sdk worker with deterministic fallback",
)
async def post_skill_suggest_dispatch(
    body: SkillSuggestDispatchRequest,
) -> dict[str, Any] | JSONResponse:
    """Worker-hop primary transport to skill_suggest; falls back to Stage-A direct."""
    request_id = uuid.uuid4().hex[:12]
    try:
        return await dispatch_skill_suggest(request_id=request_id, body=body)
    except HTTPException:
        raise
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
