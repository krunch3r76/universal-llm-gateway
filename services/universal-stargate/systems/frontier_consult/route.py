"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
    build_dispatch_body,
)

team_router = APIRouter(prefix="/api/v1/team", tags=["team"])
frontier_router = APIRouter(prefix="/api/v1/frontier", tags=["frontier"])
logger = get_logger(__name__)

_FORWARD_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)


class TeamGenerateBody(BaseModel):
    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    agent: str
    model: str | None = None
    system: str = ""
    tools: list[str] | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


class FrontierGenerateBody(BaseModel):
    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    model: str
    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


# ---- dispatch-surface-split Phase 1: op-discriminated body models ----


class _DispatchCommon(BaseModel):
    """Shared fields across all op variants — not instantiated directly."""

    model_config = {"extra": "forbid"}

    messages: list[dict[str, Any]]
    system: str = ""
    tools: list[str] | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


class TeamDispatchGenerateBody(_DispatchCommon):
    """``team_dispatch`` with ``op="generate"`` — result returned inline via poll."""

    op: Literal["generate"]
    agent: str
    model: str | None = None
    # thread / subject MUST NOT appear — extra="forbid" rejects any caller that
    # supplies them (schema-level enforcement per Phase 0 contract).


class TeamDispatchToThreadBody(_DispatchCommon):
    """``team_dispatch`` with ``op="to_thread"`` — result posted to agent-bus thread."""

    op: Literal["to_thread"]
    agent: str
    thread: str
    subject: str | None = None
    model: str | None = None
    # result_delivery MUST NOT appear — derived from thread + agent; extra="forbid"
    # rejects any caller that supplies it.


# FastAPI resolves the union via the ``op`` discriminator key.
TeamDispatchBody = Annotated[
    TeamDispatchGenerateBody | TeamDispatchToThreadBody,
    Field(discriminator="op"),
]


class FrontierDispatchGenerateBody(_DispatchCommon):
    """``frontier_dispatch`` with ``op="generate"`` — persona-free, result inline."""

    op: Literal["generate"]
    model: str


class FrontierDispatchToThreadBody(_DispatchCommon):
    """``frontier_dispatch`` with ``op="to_thread"`` — persona-free, result on thread."""  # noqa: E501

    op: Literal["to_thread"]
    model: str
    thread: str
    subject: str | None = None


FrontierDispatchBody = Annotated[
    FrontierDispatchGenerateBody | FrontierDispatchToThreadBody,
    Field(discriminator="op"),
]


def _derive_subject(
    body: TeamDispatchToThreadBody | FrontierDispatchToThreadBody,
) -> str | None:
    """Auto-derive subject from last user message when not supplied."""
    if body.subject:
        return body.subject
    # Find the last user-role message and use first 80 chars of content
    for msg in reversed(body.messages):
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            return content[:80]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        return text[:80]
    return None


def _normalize_op_body(
    body: TeamDispatchGenerateBody
    | TeamDispatchToThreadBody
    | FrontierDispatchGenerateBody
    | FrontierDispatchToThreadBody,
) -> dict[str, Any]:
    """Translate a discriminated dispatch body into ``FrontierGenerateRequest`` kwargs.

    Phase 1 coexistence: ``to_thread`` calls populate BOTH the legacy
    ``result_delivery`` struct (consumed by ``async_tracker_delivery.py``) AND
    the new ``output_contract`` / ``target_thread`` / ``op`` tracker fields.
    Phase 2 will remove the legacy ``result_delivery`` write once reply-observation
    replaces the envelope-turn delivery path.
    """
    common: dict[str, Any] = {
        "messages": body.messages,
        "system": body.system,
        "tools": body.tools,
        "reasoning_effort": body.reasoning_effort,
        "generation_options": body.generation_options,
        "max_tool_turns": body.max_tool_turns,
        "transcript_id": body.transcript_id,
        "remote_mcp": body.remote_mcp,
        "caller_agent": body.caller_agent,
        "timeout_seconds": body.timeout_seconds,
    }

    # Carry agent / model depending on variant
    if hasattr(body, "agent"):
        common["agent"] = body.agent
    if hasattr(body, "model"):
        common["model"] = body.model

    if body.op == "generate":
        common["output_contract"] = "inline"
        common["op"] = "generate"
        return common

    # op == "to_thread"
    thread: str = body.thread  # type: ignore[attr-defined]
    subject = _derive_subject(body)  # type: ignore[arg-type]

    # Derive legacy ``result_delivery`` from the op arguments (Phase 0 § Bus-Mode
    # Argument Derivation). ``bus_from_agent``: persona identity for team_dispatch,
    # caller_agent for frontier_dispatch (no persona). ``bus_to_agent``: caller.
    if hasattr(body, "agent"):
        bus_from = body.agent  # type: ignore[attr-defined]
    else:
        bus_from = body.caller_agent or "cursor"

    result_delivery: dict[str, Any] = {
        "bus_thread": thread,
        "bus_from_agent": bus_from,
        "bus_to_agent": body.caller_agent or "all",
    }
    if subject:
        result_delivery["bus_subject"] = subject

    common["result_delivery"] = result_delivery
    common["output_contract"] = "thread"
    common["target_thread"] = thread
    common["op"] = "to_thread"
    return common


async def _dispatch(
    req: FrontierGenerateRequest, response: Response
) -> dict[str, Any] | JSONResponse:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)

        def _publish_event(event: Any) -> None:
            if event_bus is None:
                return
            event_bus.publish_from_sync(event)

        dispatch_body = await build_dispatch_body(req, event_publisher=_publish_event)
    except FrontierEndpointError as exc:
        logger.warning(
            "dispatch rejected: request_id=%s field=%s reason=%s",
            exc.request_id,
            exc.field,
            exc.reason,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_FORWARD_TIMEOUT
    ) as client:
        forward = await client.post("/api/v1/pipelines/dispatch", json=dispatch_body)

    response.status_code = forward.status_code
    try:
        return forward.json()
    except ValueError as exc:
        logger.error(
            "dispatch forward returned non-JSON: status=%s error=%s",
            forward.status_code,
            exc,
        )
        return {
            "error": {
                "code": "dispatch_invalid_response",
                "message": forward.text[:500],
            }
        }


@team_router.post("/generate", status_code=202, response_model=None)
async def team_generate(
    body: TeamGenerateBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch (team-seat consults)."""
    req = FrontierGenerateRequest(**body.model_dump())
    return await _dispatch(req, response)


@frontier_router.post("/generate", status_code=202, response_model=None)
async def frontier_generate(
    body: FrontierGenerateBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free dispatch (raw engine)."""
    req = FrontierGenerateRequest(**body.model_dump())
    return await _dispatch(req, response)


# ---- dispatch-surface-split Phase 1: op-discriminated routes ----


@team_router.post("/dispatch", status_code=202, response_model=None)
async def team_dispatch(
    body: TeamDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; the agent's reply lands on
      ``thread``; tracker terminal status reflects observed reply (Phase 2).

    Use ``frontier_dispatch`` for raw engine calls without a persona.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    return await _dispatch(req, response)


@frontier_router.post("/dispatch", status_code=202, response_model=None)
async def frontier_dispatch(
    body: FrontierDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; model's reply lands on ``thread``.

    Use ``team_dispatch`` for persona-aware dispatch with agent seat assignment.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    return await _dispatch(req, response)
